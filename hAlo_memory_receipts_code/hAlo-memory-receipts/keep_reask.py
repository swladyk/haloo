"""Keeps the answers Fix 2 already got, without asking Reactor anything.

    python keep_reask.py test_results_cited.jsonl test_results_v2.jsonl.reask --out test_results_v2.jsonl

Every "I don't know" that the second pass has already turned into a real answer is replaced; everything else is copied
unchanged. Fix 2 can keep running in another window while you do this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").split("\n"):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass  # empty line, or a line still being written
    return out


def is_abstain(r: dict) -> bool:
    return (r.get("status") != "answered" or r.get("abstained")
            or "INSUFFICIENT_EVIDENCE" in str(r.get("answer", "")) or not str(r.get("answer", "")).strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("reask", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    new = {r["question_id"]: r for r in jsonl(args.reask) if r.get("question_id") and not is_abstain(r)}
    merged, replaced = [], 0
    for r in jsonl(args.results):
        n = new.get(r.get("question_id"))
        if is_abstain(r) and n:
            r = n
            replaced += 1
        merged.append(r)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")
    print(f"kept {replaced} new answer(s) from the second pass; {len(merged)} result(s) written to {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
