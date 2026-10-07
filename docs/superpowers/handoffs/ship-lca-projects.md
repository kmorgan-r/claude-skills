# Ship handoff: lca-projects

- **Topic:** lca-projects
- **Branch:** `feat/lca-projects` (worktree `.claude/worktrees/lca-projects`)
- **PR:** https://github.com/kmorgan-r/claude-skills/pull/46
- **Spec:** `docs/superpowers/specs/2026-10-07-lca-projects-design.md`
- **Plan:** `docs/superpowers/plans/2026-10-07-lca-projects.md`. Its Handoff section holds the install and smoke steps.
- **Review profile:** `/ship` with the plan-review rounds skipped (user instruction). P1 and P3 did not run.
- **Final status:** awaiting a human merge. The conductor never merges.

## Pipeline

| Phase | Outcome |
|---|---|
| P0 init | Reused the existing linked worktree on `feat/lca-projects`, cut from `main` at dc37d5f. The session ran from another repo, so git ran with `-C <worktree>`. |
| P1 spec review | Skipped by user instruction. |
| P2 plan | 4 tasks. Every file was pre-verified in a scratch folder and copied byte-identical. The spike showed a plugin cannot approve its own `$.tool.check`, so background refresh needs a permission rule. |
| P3 plan review | Skipped by user instruction. |
| P4 implementation | See the P4 section below. |
| P5 PR | #46 opened against `main`. The branch was pushed by name only. |
| P6 fix-pr-reviews | The first bot review on 1f33ebf said "✅ No critical issues found" (run 37633532615, success). Loop Complete, no urgent issues, 0 fix rounds. |
| P6.5 db-gates | No DB artifacts (nothing under `supabase/`). `db_gate` is null. |

### P4 detail

- **Tasks 1–3** (copy and gate) ran on the ollama worker, each followed by an opus task review.
  - The Task 2 worker blocked: its `CLAUDE_CONFIG_DIR` points at the worker profile, which has no transcripts. The conductor finished the step inline. `backfill.ts` now fails loudly on a config folder with no transcripts.
- **Task 4** (bootstrap outside the repo) ran on Anthropic only, because it handles client data.
- **Task-review fix rounds:**
  - the session is followed across `/clear`;
  - slugs carry over, and a deleted company stays as `gone`;
  - an unreadable `status.json` is never overwritten;
  - a `launch_cwd` holding `;` or a quote is ignored;
  - the skill's wording was tightened.
- **The repo is public.** Before the first push, the branch was rewritten (`git filter-branch`) to replace a client's real identifiers with fictional ones.
  - `git log -p origin/main..HEAD` was scanned before each push: 0 identifiers.
  - The pre-rewrite history survives locally as `refs/original/refs/heads/feat/lca-projects`; see After merge.
- **Whole-branch review (opus):** "With fixes", 0 Critical, 3 Important, 9 Minor.
  - Merge 2056837 brought in `origin/main` (README conflict, both sides kept).
  - Fix commit 844139e: no nudge for headless runs, slug folding for `ø æ ß đ ł`, the pane notes, product UUID → company, the `lca-save` company check, four tests, and the handoff steps.
  - The scoped re-review found 11 of 12 addressed. The 12th (the old slug example's real company name, still in early branch commits) was ruled acceptable. The re-review raised three new Minors, all ruled (see Rulings). Doc fix: 1f33ebf.

**Exit gate (from the worktree root):**
- `claude plugin test ./lca-projects`: 34 pass, 0 fail.
- `tsc -p lca-projects/tsconfig.json`: exit 0.
- `claude plugin validate ./lca-projects`: passed, with the one expected warning (no `author`).

## Rulings made during execution

- **Fix rounds were authored by the conductor.** The ollama worker is a copy-worker and cannot be resumed, and the fixes needed judgement about engine semantics.
- **The plan's code blocks stay as written.** A note under Task 1 says the branch head is authoritative.
- **An unreadable `status.json` is never overwritten.** The spec's §9 row was amended to match: slug stability protects which folder holds which brief.
- **The client identifiers were scrubbed by rewriting history.** The rewrite was done before any push, then scanned.
- **`origin/main` was merged in, not rebased.** A second rewrite would have orphaned every hash in the ledger and the state files.
- **The nudge's `session.append` stays before `next(e)`.** The placement is unproven either way; smoke step 2 decides it. If it breaks the turn, move the observer's work after `next(e)`: a `.catch` replays a settled `next`, so the tool does not run twice.
- **The nudge is gated on `session.start`'s `isInteractive`.** VS Code and the desktop app may report it as false; smoke step 2 checks this and names `$.session.surfaces()` as the alternative.
- **The pane note for a call that rejects has no test.** The test kit skips a hook that throws instead of rejecting the call.
- **`refs/original` was not deleted by the conductor.** The auto-mode classifier refused it.

## Leftovers, deliberately not fixed

- **Test companies show in the Active list:** Test Company, UPSERT_NULL_TEST_CO and ClimatePoint, because dev sessions touched them. Archive them on the dashboard, or add a hide action later.
- **Slugs drop `ð þ œ ı`.** Slugs are frozen once assigned, so a later fold moves no folder.
- **A refusal at the pane's permission dialog reads "failed", not "denied"**: the engine reports it as an errored call.
- **Two concurrent recomputes can both nudge the same company once.**
- **Observer file I/O runs before the tool call (`next(e)`)**, and each recompute re-reads every session file. That is fine at today's ~60 sessions.
- **Unreadable session files:** if another session's file is unreadable at refresh time, its companies are marked `gone` until the next refresh.
- **Untested paths:**
  - a `/clear` landing inside a recompute;
  - background refresh firing on the 15-minute timer under an allow rule.
- **The first `/lca-save` pins `launch_cwd`** to that session's folder.
- **`plugin.json` has no `author`**, as in the sibling mods.

## Live-only checks (smoke test, plan Handoff)

Install pre-merge per the plan:
- add the worktree's `lca-projects` folder to `CLAUDE_CODE_PLUGIN_DIRS`;
- junction `lca-save` into `~/.claude/skills`.

Then, in a fresh session:
1. `/lca`: does the pane refresh run, or note a denial?
2. Call a ClimatePoint tool on a real client product. **The next model turn must go on with no API error** (this is where the nudge lands mid tool call). Repeat from VS Code or the desktop app.
3. Pick the company in the pane. A seeded tab opens, it registers, and `launch.cmd` names the native `claude.exe`.
4. Run `/lca-save` in that tab.
5. Archive a test company; it should drop off the list.

## After merge (manual, with the user)

1. **Delete the pre-scrub backup ref:** `git update-ref -d refs/original/refs/heads/feat/lca-projects` in `C:\Users\kmorg\claude-skills-main`. It names the real client. Until it is gone, never `git push --mirror` or push `refs/*` from that clone.
2. **Re-point the install to the main checkout** after `main` is pulled:
   - the `CLAUDE_CODE_PLUGIN_DIRS` entry → `C:\Users\kmorg\claude-skills-main\lca-projects`;
   - the `lca-save` junction → `C:\Users\kmorg\claude-skills-main\lca-save`.
3. **Delete `C:\Users\kmorg\lca-projects-verified\`.** It holds the pre-verified files and client data.
