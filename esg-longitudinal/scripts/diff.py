#!/usr/bin/env python3
"""Diff two snapshot CSVs and emit a change report (markdown).

Matches rows on (entity, indicator, period) so you compare like with like:
- NEW:     key present now, absent before (e.g. a freshly disclosed year)
- CHANGED: same key, different value (restated / corrected figures)
- DROPPED: present before, gone now (disclosure withdrawn)

This is the payoff of re-running later: it shows what moved.

Usage:
    python diff.py --old data/snapshots/2026-06-29.csv \
                   --new data/snapshots/2027-06-29.csv --out reports/change_2027.md
"""
import argparse
import csv
import os

KEY = ("entity", "indicator", "period")


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return {tuple(r.get(k, "") for k in KEY): r for r in csv.DictReader(f)}


def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    old, new = load(args.old), load(args.new)
    added = [k for k in new if k not in old]
    dropped = [k for k in old if k not in new]
    changed = [k for k in new if k in old and new[k].get("value") != old[k].get("value")]

    L = ["# ESG change report\n",
         f"- old snapshot: `{args.old}` ({len(old)} rows)",
         f"- new snapshot: `{args.new}` ({len(new)} rows)",
         f"- new: {len(added)} | changed: {len(changed)} | dropped: {len(dropped)}\n"]

    if changed:
        L += ["## Changed values\n",
              "| entity | indicator | period | old | new | delta |",
              "|---|---|---|---|---|---|"]
        for k in sorted(changed):
            o, n = old[k].get("value", ""), new[k].get("value", "")
            do, dn = num(o), num(n)
            delta = f"{dn - do:+g}" if (do is not None and dn is not None) else ""
            L.append(f"| {k[0]} | {k[1]} | {k[2]} | {o} | {n} | {delta} |")
        L.append("")

    if added:
        L += ["## Newly disclosed\n",
              "| entity | indicator | period | value |",
              "|---|---|---|---|"]
        for k in sorted(added):
            L.append(f"| {k[0]} | {k[1]} | {k[2]} | {new[k].get('value', '')} |")
        L.append("")

    if dropped:
        L.append("## Dropped\n")
        for k in sorted(dropped):
            L.append(f"- {k[0]} / {k[1]} / {k[2]} (was {old[k].get('value', '')})")
        L.append("")

    if not (added or changed or dropped):
        L.append("_No differences between the two snapshots._")

    report = "\n".join(L)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.out}\n")
    print(report)


if __name__ == "__main__":
    main()
