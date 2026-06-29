# Ship handoff — linkedin-find-leads

**Topic:** linkedin-find-leads — LinkedIn-native B2B lead sourcer → Odoo `mailing.contact` rows.
**Branch:** `feat/linkedin-find-leads`
**PR:** https://github.com/kmorgan-r/claude-skills/pull/9
**Final review status:** Internal whole-branch review (most-capable model) = READY TO MERGE (0 Critical / 0 Important). PR `claude-review` CI: 5 robustness findings applied; 1 remaining accepted-by-design (see below).
**Tests:** `python -m pytest linkedin-find-leads/scripts/test_linkedin_lead_finder.py` → 62 passed (hermetic; no network, no `~/marketing` dependency).

## Phase log
- **init** — branch off fresh `main` (ff +64); state-file `.gitignore` (44d38cf); spec relocated from HOME repo + committed (f72eee2).
- **spec-review** — reviewing-plans auto ×2, 5/5 reviewers both passes; 0 unresolved CRITICAL (74a0f95, 6c891d1).
- **writing-plans** — 13 TDD tasks (7ecf10e).
- **plan-review** — reviewing-plans auto ×2, 5/5 both passes; 2 CRITICAL + ~10 IMPORTANT + ~8 MINOR applied pass 1, 0 CRITICAL pass 2 (64926b1, 7aaddae).
- **implementation** — subagent-driven TDD: group A scripts (fb2fa48, c29b38a), group B docs (d1fe125), top_skills cleanup (b10dcaa); per-task reviews clean; final whole-branch review READY TO MERGE; exit gate 59 passed.
- **pr-create** — pushed; PR #9 opened.
- **fix-pr-reviews** (executed manually; the repo `fix-pr-reviews` skill is not invocable on this machine) — `claude-review` GitHub Action review loop:
  - 8fb332a — create output/checkpoint dirs before writing (HIGH).
  - 628e59d — guard `client.last_rate` on the enrich success path (HIGH).
  - 5d52358 — cross-platform `MARKETING_DIR` default `~/marketing` (HIGH).
  - eb85e6e — `--schema-manifest` missing path now errors instead of silently skipping (HIGH).
  - Loop reached the MAX-5 iteration bound; remaining finding accepted-by-design (below).

## Remaining open finding — ACCEPTED BY DESIGN (human decision at merge)
**`claude-review` HIGH: `_PARSE_ERRORS` includes `AttributeError`, which could swallow a "missing client method" programming bug.**
- `AttributeError` was added deliberately in plan-review (a CRITICAL finding) so a non-dict / wrong-shape `get_profile` response (`123.get(...)`) skips that one lead instead of aborting the whole batch.
- It cannot be cleanly narrowed: catching `ConnectSafelyError` specifically would require importing it, which breaks the hermetic-test design (the enrich loop intentionally classifies by exception type + message and never imports `connectsafely`). The `except Exception` API branch — required to catch `ConnectSafelyError` without importing it — would catch a missing-method `AttributeError` regardless.
- Failure mode is safe: per-row `try` + the authoritative live-quota floor (`cs.last_rate.remaining`) mean a swallowed error skips one lead (recorded in `enrich_error`), never aborts the batch or over-spends the shared budget. A genuinely missing client method is a programming bug the 62-test suite catches.
- **Recommendation:** accept as-is. If the reviewer's preference is to be honored, the cleanest path is a future refactor that duck-types the API-error class rather than importing it.

## Other known non-blockers (from the internal final review)
- "CONFIRM AT IMPLEMENTATION" markers flag real-API shapes unverifiable offline (`search_companies` wrapper/id keys; `cs.last_rate.remaining`/`.reset` attributes; whether `reset` is absolute epoch vs seconds-remaining). Code fails loud rather than coercing garbage; confirm on first live run.
- `_is_cap` matches `429`/`rate limit` as a substring of the (untrusted) error body — low risk, safe failure mode.

## Unrelated finding surfaced during this run (different repo)
The push security sweep flagged an XSS in `nothing_is_everything/site/components/BuyBlock.tsx` (user-controlled `?buyUrl=` → `<Link href>` with no scheme check → `javascript:` URI). That file is NOT part of this PR (separate project). Worth fixing there: validate the override scheme to http/https or allowlist retailer hosts.

## Leftovers
None blocking. The PR is mergeable; the human merges manually (ship never auto-merges).
