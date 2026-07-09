# Ship handoff — esg-xlsx-legend-export

**Topic:** ESG Longitudinal — Excel workbook export with a Legend tab
**Branch:** `feat/esg-xlsx-legend-export` (off `main` @ cd1a818)
**PR:** https://github.com/kmorgan-r/claude-skills/pull/20 → base `main`
**Final status:** READY TO MERGE — awaiting human merge (conductor never merges).

## What shipped

New `esg-longitudinal/scripts/export_xlsx.py` (openpyxl): renders a canonical snapshot
CSV into a formatted `.xlsx` with a colored **Data** sheet (frozen header, autofilter,
status/SMART cell fills) and an explanatory **Legend** sheet (column dictionary + code
tables + color key). Wired into `SKILL.md` as **Step 10**. CSV stays the canonical,
diff-able source of truth; `snapshot.py`/`diff.py` untouched (zero diff). `.gitignore`
ignores `**/reports/*.xlsx`.

## Pipeline record

- **P1 spec-review:** reviewing-plans auto ×2 (3/3 reviewers each). Pass1 applied 2 Critical
  + 9 Important + 5 Minor; Pass2 applied 2 Important + minors. No unresolved Critical.
- **P2 writing-plans:** 5-task TDD plan (`docs/superpowers/plans/2026-07-09-esg-xlsx-legend-export.md`).
- **P3 plan-review:** reviewing-plans auto ×2 (3/3 each). Pass1 applied 1 Critical
  (module-top openpyxl import breaking collection) + 2 Minor; Pass2 all reviewers clean.
- **P4 implementation:** subagent-driven (sonnet), 5 tasks TDD, commits `b75b0f0..11e0b8a`.
  E2E smoke on the real 29-col Philips snapshot: 69 rows, sheets [Data, Legend], A1:AC70,
  freeze A2, header `entity` (BOM stripped), all status cells colored. Final independent
  whole-branch review (opus): READY TO MERGE, 0 Critical / 0 Important, 6 Minor — 3 fixed
  (`6ac1a22`: dead test loop, legend swatch labels, comment), 3 deferred (below).
- **P5 pr-create:** pushed, opened PR #20.
- **P6 fix-pr-reviews:** skill not installed → ran essence manually. Repo `claude-review`
  Action flagged **1 HIGH: Excel formula injection (CWE-1236)** — snapshot free-text values
  (from untrusted report PDFs) written unescaped; openpyxl types a leading-`=` string as a
  live formula in a shareable `.xlsx`. **Fixed** (`3101565`) by forcing snapshot-sourced
  Data cells to inert string type (`data_type="s"`) WITHOUT mutating the verbatim value
  (provenance preserved), proven by a save/reload test. Re-review: **✅ No critical issues
  found**, mitigation confirmed with no bypass. All-clear.
- **P6.5 db-gates:** no DB artifacts (no `supabase/`) → none.
- **P7:** this handoff.

## Tests

79 pytest pass (43 pre-existing + 36 new incl. the injection round-trip test);
`snapshot.py`/`diff.py` zero diff.

## Leftovers (deferred Minor findings — non-blocking, triage at leisure)

1. `test_autofilter_covers_full_range` re-derives the range with the same arithmetic the
   impl uses (not an independent oracle) — code live-verified correct (A1:AC70 / A1:AD3).
2. `MAX_WIDTH` clamp in `export_xlsx.py` is currently a no-op (all widths ≤ 60) — harmless
   defensive code guarding future wide entries.
3. `import pytest` / `import sys` / `import pathlib` sit mid-file in the test (artifact of
   additive TDD tasks) — cosmetic; the load-bearing "no module-scope openpyxl import" note
   is intentional and must stay.

## DB gate

None. No migration applied ahead of merge → no rollback obligation if the PR is abandoned.

## Remaining action

**Human merges PR #20 to `main`.** On the next `/ship` invoke, ship checks
`gh pr view 20 --json state`; MERGED with a clean/null db_gate → status done.
