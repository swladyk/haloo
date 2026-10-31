"""Adds citations to answers where Reactor gave the answer but did not name the chat it came from.

    python add_citations.py test_results.jsonl --questions test/questions.jsonl --histories test/histories.jsonl \
        --out test_results_cited.jsonl
    python predictions.py test_results_cited.jsonl --histories test/histories.jsonl --out predictions.jsonl \
        --abstain-on agent,no-answer

The answers stay exactly as Reactor wrote them. For an answered, uncited question we re-run our own search (the same
one that built the evidence Reactor saw) and cite, among the chats we sent, the ones whose text contains the answer;
if none contains it word for word, the one chat sharing the most words with the answer and the question.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_run2 as fr  # noqa: E402  our search: retrieve() and tokens()

LABELS = re.compile(r"\s*\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def pick(answer: str, question: str, evidence: list[dict]) -> list[str]:
    a = norm(LABELS.sub("", answer).split("\n")[0])
    exact = [e["id"] for e in evidence if a and len(a) >= 3 and a in norm(e["text"])]
    if exact:
        return exact[:2]
    want = set(fr.tokens(answer)) | set(fr.tokens(question))
    best = max(evidence, key=lambda e: len(want & set(fr.tokens(e["text"]))), default=None)
    return [best["id"]] if best else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--histories", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    questions = {q["question_id"]: q for q in fr.jsonl(args.questions)}
    histories = {h["history_id"]: h for h in fr.jsonl(args.histories)}
    added = kept = 0
    out = []
    for r in fr.jsonl(args.results):
        q = questions.get(r.get("question_id"))
        if (q and r.get("status") == "answered" and not r.get("abstained")
                and r.get("answer") != "INSUFFICIENT_EVIDENCE" and not r.get("citations")):
            evidence = fr.retrieve(q, histories[q["history_id"]])
            ids = pick(str(r.get("answer", "")), q["question"], evidence)
            if ids:
                r["citations"] = [{"label": "auto", "id": i} for i in ids]
                added += 1
        elif r.get("citations"):
            kept += 1
        out.append(r)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    print(f"citations added to {added} answers; {kept} already had Reactor's own; written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
