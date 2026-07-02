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


TKEY = ("entity", "indicator")


def _latest_by(snap, status):
    """Latest-period row per (entity, indicator) filtered to a status."""
    out = {}
    for r in snap.values():
        if str(r.get("status", "")).strip() != status:
            continue
        k = tuple(r.get(x, "") for x in TKEY)
        cur = out.get(k)
        if cur is None or str(r.get("period", "")) > str(cur.get("period", "")):
            out[k] = r
    return out


def target_rows(snap):
    """Latest-period status=target row per (entity, indicator)."""
    return _latest_by(snap, "target")


def latest_found(snap):
    """Latest-period status=found actual per (entity, indicator)."""
    return _latest_by(snap, "found")


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

    old_t, new_t = target_rows(old), target_rows(new)
    new_targets = [k for k in new_t if k not in old_t]
    dropped_targets = [k for k in old_t if k not in new_t]
    changed_targets = [k for k in new_t if k in old_t and (
        new_t[k].get("target_end_year") != old_t[k].get("target_end_year")
        or new_t[k].get("value") != old_t[k].get("value"))]
    found_new = latest_found(new)
    pairs = [k for k in new_t if k in found_new]

    if new_targets or changed_targets or dropped_targets or pairs:
        L.append("## Target movements\n")
        if new_targets:
            L.append("**New targets**\n")
            for k in sorted(new_targets):
                t = new_t[k]
                L.append(f"- {k[0]} / {k[1]}: {t.get('value','')} by "
                         f"{t.get('target_end_year','') or '?'} "
                         f"(status: {t.get('target_status','') or 'n/a'})")
            L.append("")
        if changed_targets:
            L.append("**Changed targets**\n")
            for k in sorted(changed_targets):
                o, n = old_t[k], new_t[k]
                bits = []
                if o.get("target_end_year") != n.get("target_end_year"):
                    bits.append(f"end year {o.get('target_end_year','') or '?'} -> "
                                f"{n.get('target_end_year','') or '?'}")
                if o.get("value") != n.get("value"):
                    bits.append(f"value {o.get('value','') or '?'} -> "
                                f"{n.get('value','') or '?'}")
                L.append(f"- {k[0]} / {k[1]}: " + "; ".join(bits))
            L.append("")
        if dropped_targets:
            L.append("**Dropped targets** (verify achieved vs abandoned)\n")
            for k in sorted(dropped_targets):
                o = old_t[k]
                L.append(f"- {k[0]} / {k[1]}: was {o.get('value','')} by "
                         f"{o.get('target_end_year','') or '?'}")
            L.append("")
        if pairs:
            L += ["**Target vs latest actual**\n",
                  "| entity | indicator | target | actual | end year | status |",
                  "|---|---|---|---|---|---|"]
            for k in sorted(pairs):
                t, a = new_t[k], found_new[k]
                L.append(f"| {k[0]} | {k[1]} | {t.get('value','')} | "
                         f"{a.get('value','')} | {t.get('target_end_year','')} | "
                         f"{t.get('target_status','') or ''} |")
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
