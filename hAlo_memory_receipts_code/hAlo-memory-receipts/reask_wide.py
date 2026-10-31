"""Fix 2: asks Reactor again, with a WIDER search, only the questions where we said "not enough evidence".

    python reask_wide.py test_results_cited.jsonl --questions test/questions.jsonl --histories test/histories.jsonl \
        --base-url https://t05.api.reactor-hackathon.rxai.dev --agent-id agt_... --out test_results_v2.jsonl

The first run sent at most 8 conversations (12,000 characters). Here we send up to 14 conversations, each a bit
shorter, filling the message almost to its 16,000-character limit. If Reactor now gives a real answer, it replaces the
abstention; if it still says INSUFFICIENT_EVIDENCE, the old result is kept. All other results are copied unchanged.
Safe to stop and run again: questions already re-asked (saved in <out>.reask) are not asked twice.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_run2 as fr  # noqa: E402  our search helpers and the client wrapper
import client as kit  # noqa: E402  only to measure the message before sending it

LIMIT = 15800  # the client refuses messages over 16,000 characters


def is_abstain(r: dict) -> bool:
    return (r.get("status") != "answered" or r.get("abstained")
            or "INSUFFICIENT_EVIDENCE" in str(r.get("answer", "")) or not str(r.get("answer", "")).strip())


def rank(question: dict, history: dict) -> list:
    query = fr.tokens(question["question"])
    units, docs = [], []
    for si, session in enumerate(history["sessions"]):
        for ti, turn in enumerate(session["turns"]):
            units.append((si, ti))
            docs.append(fr.tokens(turn["content"]))
    scores = fr.bm25_scores(docs, query)
    per_session: dict[int, list[tuple[float, int]]] = {}
    for (si, ti), s in zip(units, scores):
        if s > 0:
            per_session.setdefault(si, []).append((s, ti))
    ranked = []
    for si, lst in per_session.items():
        lst.sort(reverse=True)
        ranked.append((lst[0][0] + 0.3 * sum(s for s, _ in lst[1:3]), si, [ti for _, ti in lst[:3]]))
    ranked.sort(reverse=True)
    return ranked


def wide_retrieve(question: dict, history: dict, budget: int, ranked: list, max_sessions: int = 14,
                  cap_top: int = 3000, cap_rest: int = 1000) -> list[dict]:
    qset = set(fr.tokens(question["question"]))
    picked, used = [], 0
    for _, si, best_turns in ranked[:max_sessions]:
        session = history["sessions"][si]
        turns = session["turns"]
        keep = set()
        for ti in best_turns:
            keep.add(ti)
            if turns[ti]["role"] == "assistant" and ti > 0:
                keep.add(ti - 1)
            elif ti + 1 < len(turns):
                keep.add(ti + 1)
            for tj in range(max(0, ti - 4), min(len(turns), ti + 5)) if len(picked) < 2 else ():
                if turns[tj]["role"] == "user":
                    keep.add(tj)
        room = min(cap_top if len(picked) < 2 else cap_rest, budget - used)
        if room < 250:
            break
        user_cost = sum(min(300, len(turns[ti]["content"])) + 8 for ti in keep if turns[ti]["role"] == "user")
        n_asst = sum(1 for ti in keep if turns[ti]["role"] == "assistant")
        asst_room = max(250, (room - user_cost) // max(1, n_asst))
        text = "\n".join(
            f"{turns[ti]['role']}: {fr.trim(turns[ti]['content'], qset, asst_room if turns[ti]['role'] == 'assistant' else 300)}"
            for ti in sorted(keep))[:room]
        picked.append({"id": session["session_id"], "date": session["date"],
                       "text": f"[conversation on {session['date']}]\n{text}", "_order": si})
        used += len(text) + 40
    picked.sort(key=lambda e: e["_order"])
    for e in picked:
        del e["_order"]
    return picked


def fitted(question: dict, history: dict) -> list[dict]:
    """The widest evidence whose whole message still fits under the client's limit."""
    ranked = rank(question, history)
    for budget in range(15000, 7000, -500):
        ev = wide_retrieve(question, history, budget, ranked)
        msg = kit.build_message(question["question"], kit.load_evidence(json.dumps(ev)), "abcd1234",
                                kit.parse_question_date(question["question_date"]))
        if len(msg) <= LIMIT:
            return ev
    return fr.retrieve(question, history)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--histories", type=Path, required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--client", type=Path, default=fr.KIT / "client.py")
    args = ap.parse_args()

    questions = {q["question_id"]: q for q in fr.jsonl(args.questions)}
    histories = {h["history_id"]: h for h in fr.jsonl(args.histories)}
    results = fr.jsonl(args.results)
    log = args.out.with_suffix(args.out.suffix + ".reask")
    new: dict[str, dict] = {}
    if log.exists():
        new = {r["question_id"]: r for r in fr.jsonl(log) if r.get("status") == "answered"}

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        qid = r.get("question_id")
        if is_abstain(r) and qid in questions and qid not in new:
            groups[questions[qid]["history_id"]].append(questions[qid])
    print(f"{sum(map(len, groups.values()))} question(s) to ask again", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp, log.open("a", encoding="utf-8") as out:
        for hid, qs in groups.items():
            h = fr.client(args, "--new-history")
            if h.get("status") != "history":
                print(f"--new-history failed: {h.get('error') or h}", file=sys.stderr)
                return 2
            for q in qs:
                ev_path = Path(tmp) / "evidence.json"
                ev_path.write_text(json.dumps(fitted(q, histories[hid])), encoding="utf-8")
                extra = ["--history", h["history_id"], "--question", q["question"], "--question-date",
                         q["question_date"], "--question-id", q["question_id"], "--evidence", str(ev_path)]
                result = fr.client(args, *extra)
                for attempt in range(6):
                    if result.get("status") == "answered":
                        break
                    why = result.get("code") or result.get("status")
                    print(f"{q['question_id']}: {why}, retrying ({attempt + 1}/6)", file=sys.stderr)
                    time.sleep(3 + 2 * attempt)
                    if why == "too_large":
                        ev_path.write_text(json.dumps(fr.retrieve(q, histories[hid])), encoding="utf-8")
                    if why in ("history_changed", "closed", "history_required"):
                        fresh = fr.client(args, "--new-history")
                        if fresh.get("status") == "history":
                            h = fresh
                            extra[1] = h["history_id"]
                    result = fr.client(args, *extra)
                result.setdefault("question_id", q["question_id"])
                out.write(json.dumps(result) + "\n")
                out.flush()
                if result.get("status") == "answered":
                    new[q["question_id"]] = result
                print(f"{q['question_id']}: {result.get('status')} {result.get('answer', '')!s:.60}", file=sys.stderr)

    replaced = 0
    merged = []
    for r in results:
        n = new.get(r.get("question_id"))
        if is_abstain(r) and n and not is_abstain(n):
            r = n
            replaced += 1
        merged.append(r)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")
    print(f"{replaced} abstention(s) now have a real answer; {len(merged)} result(s) written to {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
