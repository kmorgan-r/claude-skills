#!/usr/bin/env python3
"""Score a Stage-Q qualification run against the gold fixtures.

Credit-free, offline. The model under test reads qualification_fixtures.json,
applies Stage Q (SKILL.md), and writes predictions as JSON: {"<id>": "<tier>"}
where tier is one of strong | possible | reject. This script grades that against
the gold labels and enforces the gate.

Gate (the spec's measurable criterion):
  - reject recall == 1.0      (every gold `reject` is predicted reject; i.e. ZERO
                               junk accepted at strong/possible). NB: this is
                               recall on the reject class, not precision — at the
                               1.0 boundary the two coincide ("no reject missed"),
                               but they diverge below it, so do not loosen the
                               threshold expecting precision-style slack.
  - fit recall      >= 0.80   (gold strong/possible predicted as strong OR possible)

Usage:
  python score_qualification.py predictions.json
  python score_qualification.py predictions.json --fixtures qualification_fixtures.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score(gold_doc: dict, predictions: dict[str, str]) -> dict:
    # Gold labels live in a SEPARATE file so the blind fixtures the model sees
    # never contain the answers. gold_doc = {"pass_gate": {...}, "gold": {id: {tier}}}.
    gate = gold_doc.get("pass_gate", {"reject_recall": 1.0, "fit_recall": 0.8})
    gold = {gid: g["tier"] for gid, g in gold_doc["gold"].items()}

    gold_reject = [gid for gid, t in gold.items() if t == "reject"]
    gold_fit = [gid for gid, t in gold.items() if t in ("strong", "possible")]

    def pred(fid: str) -> str:
        return (predictions.get(fid) or "").strip().lower()

    # Reject recall: every gold reject must be predicted reject (zero junk leaks up
    # to strong/possible). `leaked` is any gold-reject the model predicted as
    # something other than reject — strong, possible, or an out-of-vocab tier —
    # which is exactly the dangerous "junk accepted" case worth surfacing.
    reject_correct = [gid for gid in gold_reject if pred(gid) == "reject"]
    leaked = [gid for gid in gold_reject if gid in predictions and pred(gid) != "reject"]
    reject_recall = len(reject_correct) / len(gold_reject) if gold_reject else 1.0

    # Fit recall: gold fits must be accepted at strong or possible.
    fit_correct = [gid for gid in gold_fit if pred(gid) in ("strong", "possible")]
    missed_fit = [gid for gid in gold_fit if pred(gid) not in ("strong", "possible")]
    fit_recall = len(fit_correct) / len(gold_fit) if gold_fit else 1.0

    missing = [gid for gid in gold if gid not in predictions]

    passed = (
        reject_recall >= gate["reject_recall"]
        and fit_recall >= gate["fit_recall"]
        and not missing
    )
    return {
        "passed": passed,
        "reject_recall": round(reject_recall, 3),
        "fit_recall": round(fit_recall, 3),
        "gate": gate,
        "leaked_junk": leaked,          # the dangerous failures
        "missed_fit": missed_fit,
        "unlabeled": missing,
        "counts": {"reject": len(gold_reject), "fit": len(gold_fit)},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Score Stage-Q predictions against gold fixtures.")
    p.add_argument("predictions", help="JSON file: {id: tier}")
    p.add_argument("--gold", default=str(HERE / "qualification_gold.json"),
                   help="Private gold-labels file (kept out of the blind fixtures).")
    args = p.parse_args(argv)

    result = score(load(args.gold), load(args.predictions))
    print(json.dumps(result, indent=2))
    if result["leaked_junk"]:
        print(f"\nFAIL: junk accepted above 'reject': {result['leaked_junk']}", file=sys.stderr)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
