"""my_benchmark.py: the kit's run_benchmark.py with our smart search (BM25 over all turns, trimmed, date-ordered)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
import math
from collections import Counter

KIT = Path(__file__).resolve().parent  # this file sits next to client.py
BUDGET = 12000          # characters of evidence per question (client limit is 16,000 incl. instructions)
MAX_SESSIONS = 8        # how many different conversations we send at most
PER_SESSION = 2400      # characters per conversation at most

STOP = set("""a an the and or but if then so of to in on at by for with from about as into over after before
is are was were be been being am do does did doing have has had having i me my mine myself we our ours you your
yours he him his she her it its they them their what which who whom whose this that these those there here when
where why how all any both each few more most other some such no nor not only own same than too very can will
just should now also could would might may must shall up down out off again further once get got gotten
tell told say said know remember mentioned mention please thanks thank ok okay yes like really much many
one two did does""".split())

WORD = re.compile(r"[a-z0-9]+")


def stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "ied", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def tokens(text: str) -> list[str]:
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def bm25_scores(docs: list[list[str]], query: list[str], k1: float = 1.4, b: float = 0.6) -> list[float]:
    n = len(docs)
    if not n:
        return []
    avg = sum(map(len, docs)) / n or 1
    df = Counter(t for d in docs for t in set(d))
    q = set(query)
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q if df[t]}
    out = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t, w in idf.items():
            f = tf.get(t, 0)
            if f:
                s += w * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        out.append(s)
    return out


def trim(text: str, query: set[str], limit: int) -> str:
    """Keep the paragraphs that match the question (plus the first line for context)."""
    if len(text) <= limit:
        return text
    paras = [p for p in text.split("\n") if p.strip()]
    scored = [(len(query & set(tokens(p))), i) for i, p in enumerate(paras)]
    keep = {0}
    for s, i in sorted(scored, reverse=True):
        if s == 0:
            break
        keep.update({i - 1, i, i + 1, i + 2})  # a matching list item with its neighbours
        if sum(len(paras[j]) for j in keep if 0 <= j < len(paras)) > limit:
            break
    out, last = [], -2
    for i in sorted(j for j in keep if 0 <= j < len(paras)):
        if i != last + 1:
            out.append("...")
        out.append(paras[i])
        last = i
    return "\n".join(out)[:limit]


def retrieve(question: dict, history: dict) -> list[dict]:
    query = tokens(question["question"])
    qset = set(query)
    units = []  # (session index, turn index)
    docs = []
    for si, session in enumerate(history["sessions"]):
        for ti, turn in enumerate(session["turns"]):
            units.append((si, ti))
            docs.append(tokens(turn["content"]))
    scores = bm25_scores(docs, query)

    per_session: dict[int, list[tuple[float, int]]] = {}
    for (si, ti), s in zip(units, scores):
        if s > 0:
            per_session.setdefault(si, []).append((s, ti))
    ranked = []
    for si, lst in per_session.items():
        lst.sort(reverse=True)
        ranked.append((lst[0][0] + 0.3 * sum(s for s, _ in lst[1:3]), si, [ti for _, ti in lst[:3]]))
    ranked.sort(reverse=True)

    picked, used = [], 0
    for _, si, best_turns in ranked[:MAX_SESSIONS]:
        session = history["sessions"][si]
        turns = session["turns"]
        keep = set()
        for ti in best_turns:
            keep.add(ti)
            if turns[ti]["role"] == "assistant" and ti > 0:
                keep.add(ti - 1)  # the user's request that the answer replies to
            elif ti + 1 < len(turns):
                keep.add(ti + 1)
            for tj in range(max(0, ti - 4), min(len(turns), ti + 5)) if len(picked) < 2 else ():
                if turns[tj]["role"] == "user":
                    keep.add(tj)  # nearby user turns are short and often hold the personal fact
        cap = 4000 if len(picked) < 2 else 1500  # the best two conversations get the most room
        room = min(cap, BUDGET - used)
        if room < 300:
            break
        user_cost = sum(min(400, len(turns[ti]["content"])) + 8 for ti in keep if turns[ti]["role"] == "user")
        n_asst = sum(1 for ti in keep if turns[ti]["role"] == "assistant")
        asst_room = max(300, (room - user_cost) // max(1, n_asst))

        def share(ti):  # user turns are short; give assistant turns the rest of the room
            return asst_room if turns[ti]["role"] == "assistant" else 400
        text = "\n".join(f"{turns[ti]['role']}: {trim(turns[ti]['content'], qset, share(ti))}" for ti in sorted(keep))
        text = text[:room]
        picked.append({"id": session["session_id"], "date": session["date"],
                       "text": f"[conversation on {session['date']}]\n{text}", "_order": si})
        used += len(text) + 40
    picked.sort(key=lambda e: e["_order"])  # oldest first: the newest mention comes last
    for e in picked:
        del e["_order"]
    return picked


def client(args: argparse.Namespace, *extra: str) -> dict:
    proc = subprocess.run([sys.executable, str(args.client), "--base-url", args.base_url, "--agent-id", args.agent_id,
                           *extra], capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"status": "error", "error": (proc.stderr or proc.stdout).strip()[-500:]}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").split("\n") if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="questions.jsonl -> client.py -> results.jsonl")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--histories", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--out", type=Path, required=True, help="results.jsonl, appended to")
    parser.add_argument("--client", type=Path, default=KIT / "client.py")
    args = parser.parse_args(argv)

    histories = {h["history_id"]: h for h in jsonl(args.histories)}
    done = set()
    if args.out.exists():  # keep only real answers; anything refused/closed/timed out is asked again
        kept, seen = [], set()
        for r in reversed(jsonl(args.out)):
            if r.get("status") == "answered" and r.get("question_id") not in seen:
                seen.add(r["question_id"])
                kept.append(r)
        args.out.write_text("".join(json.dumps(r) + "\n" for r in reversed(kept)), encoding="utf-8")
        done = seen
    groups: dict[str, list[dict]] = defaultdict(list)
    for q in jsonl(args.questions):
        if q["question_id"] not in done:
            groups[q["history_id"]].append(q)
    print(f"{sum(map(len, groups.values()))} question(s) in {len(groups)} dataset histories to ask", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp, args.out.open("a", encoding="utf-8") as out:
        for dataset_history, questions in groups.items():
            new = client(args, "--new-history")  # one Reactor history per dataset history
            if new.get("status") != "history":
                print(f"--new-history failed: {new.get('error') or new}", file=sys.stderr)
                return 2
            for q in questions:
                evidence = Path(tmp) / "evidence.json"
                evidence.write_text(json.dumps(retrieve(q, histories[dataset_history])), encoding="utf-8")
                extra = ["--history", new["history_id"], "--question", q["question"],
                         "--question-date", q["question_date"], "--question-id", q["question_id"]]
                if json.loads(evidence.read_text(encoding="utf-8")):
                    extra += ["--evidence", str(evidence)]
                result = client(args, *extra)
                for attempt in range(6):  # a dropped or replaced chat is not an answer: ask again
                    if result.get("status") == "answered":
                        break
                    why = result.get("code") or result.get("status")
                    print(f"{q['question_id']}: {why}, retrying ({attempt + 1}/6)", file=sys.stderr)
                    time.sleep(3 + 2 * attempt)
                    if why in ("history_changed", "closed", "history_required"):
                        fresh = client(args, "--new-history")  # someone else started a new chat on our agent
                        if fresh.get("status") == "history":
                            new = fresh
                            extra[1] = new["history_id"]
                    result = client(args, *extra)
                result.setdefault("question_id", q["question_id"])
                out.write(json.dumps(result) + "\n")
                out.flush()
                print(f"{q['question_id']}: {result.get('status')} {result.get('answer', '')!s:.60}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
