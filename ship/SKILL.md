---
name: ship
description: Conductor that drives the post-brainstorm dev pipeline hands-off — spec-review, plan, plan-review, implementation, PR, fix-pr-reviews loop — stopping only on failure, a DB-deploy gate decision, or final merge. Use after /superpowers:brainstorming when a committed spec exists, or to resume an in-progress pipeline.
---

# Ship — Pipeline Conductor

Drives the mechanical tail of development after brainstorming. Invoked once;
runs phases P0–P7 (with P6.5 db-gates between P6 and P7) hands-off, resuming after
any `/clear` or auto-compact via a state file.

> This is a Claude Code skill (an instruction set Claude follows at runtime).
> **Authority over control flow:** when a delegated skill ends with a hand-off
> prompt (e.g. reviewing-plans' "ready to execute?", brainstorming auto-invoking
> writing-plans), DO NOT stop or follow it — continue to the next phase as
> defined here. You (the conductor) own the sequence.

## Compact Instructions

> Preserved during auto-compaction. After ANY compaction, immediately:
> 1. Read `.claude-ship-state.json` (repo root).
> 2. Resume at `phase` using `focus_next`.
> 3. Preserve: `topic`, `branch`, `phase`, `status`, `pr`, `plan`, `blockers`, `db_gate`, `review_passes`.
> If `phase == "fix-pr-reviews"`, the loop internals belong to fix-pr-reviews
> (`.claude-pr-fix-state.json`) — defer to it; re-enter with `--loop --continue`.
> If `status == "awaiting-db-gates"`, the P6.5 DB gate was deferred — surface
> `db_gate.checklist`, require explicit human ack that the gate ran, and do NOT
> close out on a MERGED PR alone (see First action).

## First action (EVERY invoke)

Read `.claude-ship-state.json`:

- **Present and `status == "done"`** → report "pipeline already complete for
  <topic>" and stop.
- **Present and `status == "blocked"`** → surface the blocker(s) verbatim and ask
  the user to clear them. Do NOT silently re-run or skip the failed phase.
- **Present and `status == "awaiting-db-gates"`** → the P6.5 DB gate was deferred
  LOUDLY, not silently. This ack authorizes a PRODUCTION DB write/deploy, so FIRST
  run `git branch --show-current`; if it ≠ state `branch` → warn about the mismatch,
  REFUSE to ack (the working tree no longer matches the pipeline the gate belongs
  to), ask the user to reconcile, and stop. NEXT, before surfacing any checklist,
  check `gh pr view <pr> --json state` — a PR abandoned while the gate sat open at
  `awaiting-db-gates` never advanced to P7, so P7's own CLOSED re-check never runs;
  this branch must catch it instead of ack-and-continuing:
    - `CLOSED` **and** `db_gate.applied_ahead_of_merge` → LOUD halt (identical to
      P7's CLOSED branch): the apply-now migration is orphaned live in prod. Surface
      that the paired rollback (`db_gate.rollback`) MUST be run; require explicit
      human confirmation it ran (or a deliberate waiver) before setting
      `status:"done"`. The pending-deploy checklist is moot for a dead PR — this
      rollback prompt is the ONLY thing that re-surfaces the obligation once the PR
      is gone. Do NOT silently close.
    - `CLOSED` **and** no applied-ahead migration → nothing was written to prod;
      report the pipeline was abandoned, set `status:"done"`, and stop (the deferred
      checklist is moot — do not ask the human to apply migrations for a dead PR).
    - `OPEN`/`MERGED` → fall through to the ack flow below.
  Otherwise surface `db_gate.checklist`
  from state VERBATIM and ask the human to confirm each listed gate ran (migration
  applied via `apply_migration`, SQL harness passed, grants/reconciliation verified,
  any edge-fn/`config.toml`/secret deploys done — including any
  `db_gate.pending_deploys` left over from an apply-now that also touched
  functions/secrets). Only on explicit confirmation: append the ack to `phase_log`,
  set `db_gate.status:"acked"` and clear `pending_deploys`, then advance — to P7 if
  not yet written, else set `status:"done"`. Without explicit confirmation do NOT
  proceed — even if `gh pr view <pr>` shows MERGED (a merged PR whose migration never
  ran, or whose edge function was never deployed, is exactly the prod-404 this phase
  exists to stop). Re-post the checklist and stop.
- **Present (in-progress)** → echo `Resuming <topic> at phase <phase>. Next:
  <focus_next>.` Run `git branch --show-current`; if it ≠ state `branch` → warn
  about the mismatch, ask the user to reconcile, and stop. Otherwise jump to the
  handler for `phase` (see Phases). If a non-done state already exists and the
  user names a DIFFERENT spec, warn (one active pipeline only) and ask before
  overwriting.
- **Absent + a committed spec exists** in `docs/superpowers/specs/` → confirm
  which spec to use (default: most recent; otherwise ask), then start at **P0**.
- **Absent + no spec** → offer to run `/superpowers:brainstorming` first.

## State file (`.claude-ship-state.json`)

Repo-root JSON, gitignored (P0 adds the `.gitignore` entry), single active pipeline. **Write it with the Write tool** (full-document overwrite — do NOT use `jq`; the local `jq` is an unusable npm shim). If a write is interrupted (e.g. by compaction) and the state file is unreadable, re-derive state from the last commit + current branch + the spec/plan rather than trusting a partial file. Shape:

```json
{
  "topic": "<slug>",
  "spec": "docs/superpowers/specs/....md",
  "plan": null,
  "branch": "feat/<slug>",
  "default_branch": "main",
  "pr": null,
  "phase": "spec-review",
  "status": "in-progress",
  "focus_next": "<1-2 sentences for the next phase>",
  "phase_log": [ { "phase": "init", "result": "branch created" } ],
  "blockers": [],
  "test_paths": [],
  "db_gate": null,
  "review_passes": { "spec-review": 0, "plan-review": 0 }
}
```

`test_paths` is the explicit list of test files the P4 gate runs (see P4). It is
populated during P4 and persists so a post-compaction resume never has to
re-infer it (and therefore never falls back to the full, always-failing suite).

`review_passes` counts **applying** review passes per review phase, so the two-pass
ceiling (P1/P3) survives `/clear` and auto-compaction. It increments **at dispatch,
not at commit** — a `--diff` pass that applies nothing has still consumed its slot;
incrementing on commit would leave the counter at 1 after an empty pass 2, weakening
the recursion guard exactly where the guards make an empty pass most likely. A
read-only verification dispatch that neither edits nor commits does NOT increment it.

`db_gate` records the P6.5 decision + outcome (`null` until P6.5 runs; then
`{ "decision": "apply-now|defer|abort", "status": "applied|deferred|acked",
"migrations": [...], "checklist": "<verbatim gate list>", "results": [...],
"pending_deploys": [...], "applied_ahead_of_merge": <bool>, "rollback": "<paired rollback file|null>" }`).
`pending_deploys` holds edge-fn/`config.toml`/secret items ship cannot deploy —
non-empty means the gate stays OPEN (`status:"deferred"`) for human ack even when
migrations were applied-now, because the single decision/status pair cannot mean
both "migrations applied" and "deploys still pending". `applied_ahead_of_merge` +
`rollback` flag a real prod write made before merge, so a later CLOSED-unmerged PR
can re-surface the orphan-rollback obligation. It survives compaction so a deferred
gate (or pending deploy) is re-surfaced and acked on a later invoke instead of
silently closing out.

Rewrite it at every phase boundary (update `phase`, `focus_next`, append to
`phase_log`). On a failure set `status:"blocked"` and append to `blockers`.

## Phases

Each phase: check preconditions → run the action (for delegated phases, invoke the
named skill via the Skill tool, overriding its hand-off; P0 and P6.5 are inline
conductor logic with no delegated skill) → write state (Write tool) → advance or block.

### P0 init

**Derive `<slug>` first** — it feeds the branch name and every git command below
(preconditions, the `checkout -b`, the rollback `branch -D`), so an unsafe value
breaks P0 with a confusing error. Take the chosen spec's filename, drop the `.md`
extension and any leading `YYYY-MM-DD-` date prefix, lowercase it, replace
spaces/underscores with hyphens, strip every character not in `[a-z0-9-]`, and
collapse repeated hyphens. Validate before using it:
```bash
SLUG=...                          # derived as described above
[ -n "$SLUG" ] && git check-ref-format --branch "feat/$SLUG" >/dev/null 2>&1 \
  || { echo "ERROR: derived slug '$SLUG' is empty or not a valid git branch name — ask the user for an explicit slug"; exit 1; }
```
Record the validated value as `topic` in state, and **quote `<slug>` in every
command** that uses it (a stray space would otherwise split the argument).

Preconditions (any failure → stop and ask, do NOT branch):
- `git status --porcelain` empty (clean tree).
- `git fetch` then ensure the local default branch is current (the default
  branch is *derived* in the Action below — do NOT assume `main`).
- Local branch absent: `git branch --list feat/<slug>` empty.
- **Remote** branch absent: `git ls-remote --heads origin feat/<slug>` empty
  (avoids a later push collision).

Action:
```bash
# Derive the repo's default branch — do NOT assume "main" (repos may use
# master/trunk/develop). Record it in state as `default_branch`; later phases
# (rollback, P4 merge-base) read it from state rather than hardcoding a name.
DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null \
  || git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
# origin/HEAD is not always set (shallow/CI clones), so guard against an empty value:
[ -z "$DEFAULT_BRANCH" ] && { echo "ERROR: could not derive default branch — run: git remote set-head origin -a"; exit 1; }
git checkout "$DEFAULT_BRANCH" && git pull --ff-only
git checkout -b feat/<slug>
# Per-run scratch state files must never be tracked (else they cause merge
# conflicts on shared repos and can leak blocker text). Ensure both are ignored.
# First guarantee .gitignore ends in a newline: `echo x >> file` on a file whose
# last line lacks a trailing \n glues the entry onto it (-> `dist.claude-ship-state.json`),
# corrupting the prior entry AND silently failing to ignore the state file. The
# tail-byte test appends a newline ONLY when one is missing (no stray blank line
# on the common case where .gitignore already ends in \n):
[ -f .gitignore ] && [ -n "$(tail -c1 .gitignore 2>/dev/null)" ] && printf '\n' >> .gitignore
grep -qxF '.claude-ship-state.json' .gitignore 2>/dev/null || echo '.claude-ship-state.json' >> .gitignore
grep -qxF '.claude-pr-fix-state.json' .gitignore 2>/dev/null || echo '.claude-pr-fix-state.json' >> .gitignore
git add .gitignore
# Only commit if .gitignore actually changed (both entries may already exist):
git diff --cached --quiet || git commit -m "chore: ignore ship/fix-pr-reviews state files"
```
Then write the initial state file (`phase:"spec-review"`, `branch`,
`default_branch`, `spec`, `topic`, `focus_next`). **Rollback:** if the state-file
write fails after the branch was created, run
`git checkout "$DEFAULT_BRANCH" && git branch -D feat/<slug>`.
Advance to P1.

### P1 spec-review

**Before any dispatch, read the ceiling.** If `review_passes["spec-review"] >= 2` →
`status:"blocked"`, blocker `P1 review ceiling reached (2 applying passes); resolve manually`,
stop. This guards EVERY applying dispatch, not just the second one — a conductor resumed
after compaction re-enters this phase at the top, and without the check here it would
dispatch a third pass unconditionally, which is the exact scenario `review_passes` exists
for. A state file written by a pre-change `/ship` has no `review_passes` key; read an absent
key as `0`.

Then invoke `reviewing-plans` via the Skill tool with args `auto --max-reviewers 3 <spec-path>`,
and increment `review_passes["spec-review"]` at dispatch. Auto mode applies findings without
pausing — **subject to reviewing-plans' guards, so "all findings" is not the same as "all
findings written"** — and returns a summary opening with two machine-readable lines
(`REVIEWERS:` then `FINDINGS:`). P1 uses a 3-reviewer panel; P3 uses the default 5.

Then follow the **pass sequence** below. It is authoritative, and it is what a
conductor resumed from the state file executes, not only a fresh one.

1. **Coverage gate FIRST**, read from `REVIEWERS: X/N succeeded (failed: …) [models]`:
   - **Total failure** (`REVIEW FAILED` / `0/N`) — a zero-findings result from total
     failure is NOT a clean review. **This branch applies to every pass, including
     `--diff`.**
   - **Quorum failure** — block if fewer than 2 reviewers succeeded, OR fewer than
     half of those dispatched succeeded, OR either always-on reviewer (General
     Quality / Test Quality) is in the failed list.
   - **N=3 rule (P1 only)** — block when the succeeding set is exactly the two
     always-on reviewers *and* a conditional reviewer was dispatched and failed. At
     N=5 the "half of dispatched" rule already guarantees a surviving conditional
     reviewer; at N=3 it does not, and without this rule a P1 review with zero domain
     coverage would pass a gate that today blocks.
   Any of these → `status:"blocked"`, `rereview:"blocked-before-decision"`, **append the
   verbatim `REVIEWERS:` and `FINDINGS:` lines plus which branch fired to `blockers`**,
   stop. **Do NOT run `--diff`** — running the cheap branch after an under-covered pass 1
   would let a clean `1/1` launder a `1/5` fan-out into a pass.
2. **Unresolved CRITICAL** — `unresolved_critical = reported_C − applied_C −
   downgraded_critical` (from the `FINDINGS:` line). `> 0` → `status:"blocked"`,
   `rereview:"blocked-before-decision"`, **append the verbatim text of every unresolved
   CRITICAL finding to `blockers`**, stop. This runs BEFORE the skip-or-diff
   decision, so a blocking CRITICAL is never reached by the cheap branch.
   **Precedence:** if `FINDINGS:` is malformed AND leaves CRITICALs unaccounted for,
   this step wins over the fail-safe in step 3a — block, do not re-review.
3. **Decide the second pass.** First matching row wins:

   | # | Condition | Decision | `rereview` |
   |---|-----------|----------|------------|
   | a | `FINDINGS:` absent, unparseable, or violating a conservation identity (no unaccounted CRITICAL) | run `--diff` | `diff-on-parse-failure` |
   | b | `downgraded_critical > 0` | run `--diff`, and copy the downgraded findings verbatim into `phase_log` | `diff-on-guard-counter` |
   | c | `dropped_unevidenced > reported_total / 3` AND `applied_total > 0` | run `--diff`, log `WARNING: guard (a) dropped <n>/<total> findings` | `diff-on-guard-counter` |
   | d | `dropped_unevidenced > reported_total / 3` AND `applied_total == 0` | `status:"blocked"`, append the dropped-finding count and both lines to `blockers`, stop | `blocked-before-decision` |
   | e | `applied_total > 0` | run `--diff` | `diff` |
   | f | otherwise | skip | `skipped` |

   Row a never skips on an unreadable signal — mirroring P6's "unrecognized output →
   block" rule. Row d blocks rather than forcing a pass because at zero applied edits
   there is no apply-commit, no diff and no applied-findings list, so `--diff` would
   review nothing and return a clean `1/1`, defeating the very mitigation it is.
   Row e includes MINOR findings deliberately: an Opus MINOR is still auto-applied
   and still mutates the file, so gating on C/I only would let a plan change with no
   re-review.
4. **Ceiling check before dispatch.** If a row a/b/c/e decision would dispatch while
   `review_passes["spec-review"] >= 2` → `status:"blocked"`, append the decision row
   and both lines to `blockers`, stop. A force that cannot run is an unreadable
   signal, and the conductor must not take the cheap branch on one.
5. If `--diff` ran (`auto --diff <spec-path>`; increment `review_passes` again):
   apply the coverage gate's **total-failure branch only** — a `1/1` result is
   correct for this mode and must not trip the quorum thresholds — then re-check
   unresolved CRITICAL. Either → `status:"blocked"` + `blockers`, stop. Else advance.
   If pass 1's applied-findings list and diff were lost to compaction, re-derive both
   from the `docs: apply review findings to <file>` commit pass 1 produced
   (`git show`) rather than dispatching `--diff` with empty inputs.

Append to `phase_log` for this phase: the verbatim `REVIEWERS:` and `FINDINGS:`
lines, `panel_size`, `claude_md_sections` (the named CLAUDE.md sections
reviewing-plans reported extracting; omit if that skill has not yet been updated),
and `rereview` (one of `skipped`, `diff`, `diff-on-parse-failure`,
`diff-on-guard-counter`, `blocked-before-decision`).

`panel_size` is **the number of reviewers actually dispatched** — the count of entries
in the `REVIEWERS:` line's model bracket, not the cap that was passed. A run matching
no conditional domain signal legitimately dispatches 2 under `--max-reviewers 3`.

Record `rereview` **positively** — inferring a skip from a missing line is
indistinguishable from an interrupted state write.

Otherwise update state (`phase:"writing-plans"`) and advance.

### P2 writing-plans

If `plan` is already set OR a plan file for `<slug>` already exists in
`docs/superpowers/plans/` (brainstorming may have auto-chained writing-plans),
**skip creation** and record the existing path. Otherwise invoke `writing-plans`
via the Skill tool; ignore any auto-chain into execution. Record the plan path in
`plan`. Advance to P3.

### P3 plan-review

Invoke `reviewing-plans` via the Skill tool with args `auto <plan-path>` — the
default 5-reviewer panel, NOT P1's `--max-reviewers 3`. Plans are far larger than
specs and P3 is the pipeline's last full-panel look before implementation.

Then run the **same pass sequence as P1** (steps 1–5 there), against
`review_passes["plan-review"]`, with one difference: the **N=3 rule does not apply**
at P3, because P3 dispatches 5 and the "half of dispatched" threshold already
guarantees a surviving conditional reviewer. Everything else — the total-failure
branch applying to every pass including `--diff`, the unresolved-CRITICAL check
before the decision, the decision table, the ceiling check, and the `phase_log`
fields — is identical.

Otherwise advance to P4.

### P4 implementation

Invoke `subagent-driven-development` via the Skill tool on the `plan` (its Task
subagents keep your context lean; subagents self-verify per task via TDD). When
it completes, run the **exit gate** — NOT the full test suite. ship runs across
repos, so a quality script may be absent; `npm run <missing>` exits 1 with
`Missing script:`, which must NOT be misrecorded as a lint failure. Run only the
scripts that exist (`npm pkg get` returns `{}` for an absent key); skip absent
ones and note the skip in `phase_log`:
```bash
[ "$(npm pkg get scripts.lint)" != "{}" ] && npm run lint
[ "$(npm pkg get 'scripts.check:types')" != "{}" ] && npm run check:types
```
then run ONLY the change's own test files. **First populate `test_paths`** in the
state file (so a later resume never re-infers them): collect the test files this
branch added or changed —
```bash
git diff --name-only --diff-filter=d $(git merge-base "$DEFAULT_BRANCH" HEAD)..HEAD -- '*.test.*' '*.spec.*'
```
(`$DEFAULT_BRANCH` is the `default_branch` recorded in state at P0 — read it from
state on resume; do NOT hardcode `main`. Git pathspec wildcards match across `/`
— unlike shell globs — so these patterns DO catch nested files like
`src/**/foo.test.ts`; verified against this repo.)
Write that list into state `test_paths`, then gate on exactly those:
```bash
# GUARD: only run a test command when test_paths is NON-EMPTY. A bare
# `vitest run` / `npm test` (empty arg list) runs the repo's FULL suite — the
# pre-existing-failure trap this gate exists to avoid. An empty test_paths is a
# legitimate case (docs/config-only change): skip the test step, gate = lint +
# check:types only. This guard MUST wrap the runner so a top-to-bottom executor
# never fires a bare run before reaching the empty-test_paths prose constraint
# below.
if [ -n "<the test_paths list>" ]; then
  # ONLY vitest can be scoped to specific files safely. Do NOT fall back to
  # `npm run test -- <paths>`: a repo's `test` script often embeds its own glob
  # (e.g. `"test": "mocha 'test/**/*.spec.js' --reporter spec"`), and appending
  # paths after `--` does NOT override that glob — it runs the FULL pre-existing
  # suite ALONGSIDE the new files. Two failure modes, both bad: pre-existing
  # failures block a correct change, OR (if they happen to pass) the gate records
  # a FALSE PASS on a change whose own tests were never isolated. So: vitest →
  # run scoped; no vitest → leave the tests UNVERIFIED and let the
  # zero-verification guard below surface it for human ack — never a silent skip,
  # never a risky full-suite run.
  if [ "$(npm pkg get devDependencies.vitest)" != "{}" ] || [ "$(npm pkg get dependencies.vitest)" != "{}" ]; then
    npx vitest run <the test_paths list>
  fi
  # else (no vitest): tests stay unrun — the zero-verification guard treats a
  # non-empty test_paths with no scoped runner as a verification gap and blocks.
fi
```
If `test_paths` is empty (the change added no tests), the gate is lint +
check:types only — **never** fall back to a full `npm test`/`vitest run` (a
repo's pre-existing failures would block every pipeline). On resume, read
`test_paths` from state rather than re-deriving it.

**Zero-verification guard:** every check here is conditional, so the gate can run
*nothing* meaningful — an unverified P4 must never look identical to a passing
one. TWO cases must block (not silently advance):
1. **Nothing ran** — the repo defines no `lint` and no `check:types` script AND
   the change added no tests (`test_paths` empty). `phase_log` note: `P4 ran no
   checks — repo defines no lint/check:types/test scripts and the change added no
   tests`.
2. **Tests exist but could not be scoped** — `test_paths` is NON-EMPTY but the
   repo has no vitest, so the change's own tests never ran (the `npm run test`
   fallback is deliberately omitted: it can't override an embedded glob without
   risking the full pre-existing suite). `phase_log` note: `P4 could not run the
   change's tests — no vitest to scope them; the repo's own test script can't be
   safely scoped`. This is the stronger gap — a change that ships tests but never
   runs them is worse than one with none.
In either case, do NOT advance: set `status:"blocked"` with blocker `P4 could not
verify the implementation; run the change's tests manually (or confirm the change
is sound), then re-invoke /ship to advance`, and stop. The human ack is the
verification of last resort.
Any failure → `status:"blocked"`, write the failing output summary to `blockers`,
stop. **P4-blocked resume:** re-invoking `/ship` resumes the failed task inside
`subagent-driven-development` (it tracks task-level progress) — do not restart the
whole plan. On success advance to P5.

### P5 pr-create

**If `pr` is already set, do NOT open another PR** — skip to P6. Otherwise invoke
`finishing-a-development-branch` via the Skill tool to push the branch and open a
PR with `gh`. Record the PR URL in `pr`. Advance to P6.

### P6 fix-pr-reviews

Do NOT reimplement the loop — delegate. `fix-pr-reviews` owns its own state
(`.claude-pr-fix-state.json`), iteration counter, and MAX-5 cap.

"Same PR" vs "different PR" is decided by the **`pr_number`** field in
`.claude-pr-fix-state.json` (that file's schema belongs to fix-pr-reviews — if it
ever renames the field, update this check to match). Compare it against the
current PR: `gh pr list --head "$(git branch --show-current)" --json number --jq '.[0].number'`.
- **Fresh entry** (no `.claude-pr-fix-state.json`, or its `pr_number` ≠ this PR) →
  invoke `fix-pr-reviews --loop`. (fix-pr-reviews itself also backs up a
  wrong-PR state file and starts fresh, so this is belt-and-suspenders.)
- **Resume into P6** (a `.claude-pr-fix-state.json` whose `pr_number` == this PR
  exists) → invoke `fix-pr-reviews --loop --continue` (preserves its counter, not
  restarted at 1).

Determine the outcome by reading fix-pr-reviews' final output block AND its state
file. **Match on the distinctive phrase, not the exact line** — fix-pr-reviews'
headings are natural-language and may drift (a dropped `!`, reworded tail, extra
whitespace, em-dash↔hyphen). Test each row's phrase as a case-insensitive
substring of the final `## Loop ...` heading; the column below gives the phrase to
look for, NOT a string to match byte-for-byte. Map (no silent fall-through):

| fix-pr-reviews outcome | detect (case-insensitive substring of the final heading) | ship outcome |
|------------------------|----------------------------------------------------------|--------------|
| all-clear | `Loop Complete` AND (`All Clear` OR `No Urgent Issues`) | advance to **P6.5** |
| max-iterations, issues remain | `Max Iterations Reached` | `status:"blocked"` |
| all-remaining-issues skipped | `Human Review Needed` (dash variant irrelevant) | `status:"blocked"` |
| unparseable review / workflow fail | the `URGENT_TOTAL=-1` stop (detect per below) | `status:"blocked"` |
| unrecognized output | none of the above phrases present | `status:"blocked"`, surface the raw fix-pr-reviews output verbatim in `blockers` |

The `URGENT_TOTAL=-1` case is **not a state-file field** — fix-pr-reviews does
not persist that sentinel (its regression step explicitly skips updating
`previous_urgent_count` on a `-1`). Detect it instead by fix-pr-reviews' own stop
block: it prints `Cannot determine issue count` (or an empty-review /
workflow-`failure`/`cancelled` message) and exits WITHOUT any all-clear heading.
Corroborate with its state file's `previous_urgent_count`: a recorded `0`
alongside an all-clear phrase confirms clean; a non-zero count with no terminal
heading means it stopped mid-flight → blocked.

Blocking on unrecognized output is deliberate — a hands-off conductor must NOT
advance to merge-ready on a signal it can't read. It stays recoverable: the human
reads the surfaced output and clears the blocker. This table is the coupling
point between the two skills; if fix-pr-reviews' headings are ever intentionally
reworded, update the phrases here.

### P6.5 db-gates

DB deploy gates (apply migration to prod, SQL/pgTAP harness, edge-fn deploy,
`supabase secrets set`) are NOT silent leftovers for the P7 handoff. This phase
makes them block or defer LOUDLY. It runs only after P6 reaches all-clear. On
entering, set `phase:"db-gates"` in state (so every sub-outcome below resumes here).

**Detect** the branch's deployable DB artifacts vs the default branch
(`$DEFAULT_BRANCH` from state; `$BASE = git merge-base "$DEFAULT_BRANCH" HEAD`):
```bash
# path-B-automatable: migrations + SQL test harness
git diff --name-only --diff-filter=d "$BASE"..HEAD -- 'supabase/migrations/*' 'supabase/tests/*'
# deploy/secret gates: surfaced, NEVER auto-applied by ship
git diff --name-only --diff-filter=d "$BASE"..HEAD -- 'supabase/functions/*' 'supabase/config.toml'
```
Both empty → `phase_log` note `P6.5 no DB artifacts`, advance to P7. Any present →
this phase MUST resolve before close-out; ship does NOT route around it.

**One explicit decision.** Present the detected artifacts + the path-B runbook,
then ask the human ONE decision. Never auto-pick: this is the single human gate in
a hands-off conductor because it writes to PRODUCTION (the frontend degrades
dark-safe on `PGRST202`, so a missed migration is invisible in CI and surfaces only
as a user-facing empty state / console 404). The options depend on what was
detected:
- **Migrations present** → **apply-now / defer / abort** (apply-now is
  migrations-only; see below).
- **Only edge functions / `config.toml` / secrets, no `supabase/migrations/*`** →
  **defer / abort** only. apply-now does not apply — ship never auto-deploys
  functions or sets secrets, so there is nothing for it to apply now.

**Prod-only environments — default-recommend defer.** When the only reachable
Supabase project is production (no staging/non-prod DB — the common case here),
apply-now means an *unmerged write to prod*, so present the options but recommend
**defer** unless the human explicitly opts into apply-now. Note the asymmetry: the
transactional `BEGIN … ROLLBACK` validation runs against prod but commits nothing
(safe), whereas `apply_migration` is a real, irreversible-without-rollback prod
write. Default to defer; make apply-now an explicit human choice.

**apply-now — migrations only, via path B.** Rails FIRST — any failing rail
downgrades this to defer (never force an unsafe prod write):
1. **Additive-only scan.** Read each new migration. It is safe to apply ahead of
   merge ONLY if it cannot break the still-deployed *old* frontend. Downgrade to
   defer (naming the offending statement) on any unsafe pattern:
   - Breaking DDL — `DROP TABLE|COLUMN|CONSTRAINT`, `ALTER … TYPE`, `SET NOT NULL`
     without a backfilled default, `RENAME`, `REVOKE` of an in-use grant.
   - `CREATE OR REPLACE FUNCTION` that changes an EXISTING RPC's signature or
     behavior — the old frontend still calls the old contract (a brand-new function
     name is additive and safe; a replacement is not).
   - Data-mutating DML — `DELETE`, `TRUNCATE`, `UPDATE` — destructive regardless of
     merge order.
   - Permission-broadening — any `GRANT … TO anon|authenticated|public`,
     `CREATE POLICY`, or `ALTER POLICY`. These widen who can read/write prod data
     and are the exact statements the post-check (`anon` absent where required) exists
     to police — but that check runs *after* the write already landed on prod. "New,
     not a REVOKE" does not make a grant safe: `GRANT SELECT ON sensitive_table TO
     anon` or `CREATE POLICY … USING (true)` is a data exposure the moment it commits.
     A human must review the specific grant/policy before it touches prod → defer.
   This list is a heuristic, NOT an exhaustive safety proof. When a migration's
   effect on the old frontend — or on who can access prod data — is unclear, DEFER.
   Only clearly additive, non-permission-broadening migrations (new
   table/RPC/column-with-default) are apply-now candidates. A new grant or policy is
   NOT categorically additive — route it to defer per the permission-broadening rail.
2. **Rollback pairing.** Confirm the paired rollback migration exists (repo
   convention: sibling `…99`, e.g. `20260702000099`). Absent → defer.
3. **Idempotency guard.** `list_migrations` (Supabase MCP); if the version is
   already applied, skip the apply and run only post-checks (safe resume).
Then the proven steps (all from P2/P4a/P4b/P5):
1. **Validate transactionally** — `execute_sql` running the migration body inside
   `BEGIN … ROLLBACK` (proves it executes, commits nothing). If a
   `supabase/tests/*.test.sql` harness shipped, run its scenarios the same way; any
   HARD-blocker assertion (e.g. benchmark-leak) failing → `status:"blocked"`, stop.
2. **Apply** — `apply_migration` (NEVER `db push`).
3. **Post-checks** — grants (`anon` absent where required, `authenticated`
   present), an RPC smoke call under an impersonated owner (RLS-real), and
   reconciliation counts.
Record each result in `phase_log` and in `db_gate` (`decision:"apply-now"`,
`migrations`, `results`, `applied_ahead_of_merge:true`, `rollback:"<paired rollback
file>"`). **Applied ahead of merge → if the PR is later abandoned the migration is
orphaned in prod and the paired rollback MUST be run**; the `rollback` field carries
the filename so P7's MERGED/CLOSED re-check (not just the handoff prose) can
re-surface it.

**apply-now does NOT cover edge functions / secrets — and does NOT close the gate
while any remain.** Detected `supabase/functions/*` or `config.toml`/secret changes
are listed but never deployed by ship (deploy + secret semantics are outside path
B's proven scope). Record them in `db_gate.pending_deploys`. The final step is then
CONDITIONAL — the single decision/status pair cannot mean both "migrations applied"
and "deploys still pending", so any pending item keeps the gate OPEN:
- **`pending_deploys` empty** → set `db_gate.status:"applied"` and advance to P7.
- **`pending_deploys` non-empty** → the migrations are applied but the gate is NOT
  clean. Set `db_gate.status:"deferred"` and state `status:"awaiting-db-gates"`, and
  build `db_gate.checklist` from the pending edge-fn/secret deploys (noting which
  migrations were already applied so the human does not re-run them). Do NOT advance
  to P7 — First action holds here until the human acks the deploys ran. This closes
  the hole where an apply-now migration would advance straight to merge-ready
  alongside an updated-but-undeployed edge function.

**defer — loud, never silent.** Set `status:"awaiting-db-gates"` (NOT
in-progress). Post the gate checklist as a PR comment (proven manually in
PR #3944): exact migrations to `apply_migration`, harness to run, grants/
reconciliation to verify, the rollback file, and any edge-fn/secret deploys. Store
the verbatim checklist in `db_gate` (`decision:"defer"`, `status:"deferred"`,
`applied_ahead_of_merge:false` — nothing was written to prod yet, so a later CLOSED
PR triggers no rollback obligation). Ship will NOT set `done` until the human acks
it ran (see First action / P7).

**abort.** `status:"blocked"`, blocker `DB gate aborted by human — resolve DB
deploy manually, then re-invoke /ship`, stop.

### P7 awaiting-merge

First `mkdir -p docs/superpowers/handoffs/` (the Write tool does not create
parent directories, and this dir may not exist on a repo that has never
completed a `ship` run). Then write the one-time committed summary to
`docs/superpowers/handoffs/ship-<slug>.md` (topic, branch, PR URL, final review
status, `phase_log`, leftovers, the P6.5 `db_gate` outcome — applied migrations +
post-check results, OR the deferred gate checklist — and, if a migration was
applied ahead of merge, an **applied-but-unmerged rollback flag** naming the paired
rollback (`db_gate.rollback`) to run if the PR is abandoned) and commit it. Report
the PR URL + status. **STOP — the human merges manually; the conductor never
merges.** On a later `/ship` invoke, check `gh pr view <pr> --json state`:
- `MERGED` **and** the gate is clean (`db_gate` null, or `status` `applied`/`acked`
  with an empty `pending_deploys`) → set `status:"done"` and stop.
- `MERGED` but `db_gate` is still deferred/unacked (`status == "deferred"` OR a
  non-empty `pending_deploys`) → do NOT set done. Switch to
  `status:"awaiting-db-gates"` and surface the checklist (a merged PR whose migration
  never ran / edge fn never deployed is the exact prod failure P6.5 prevents); handle
  per First action until acked. **Defense-in-depth note:** a deferred/pending gate
  normally holds the pipeline at `status:"awaiting-db-gates"`, which First action
  intercepts BEFORE this P7 handler is ever reached — First action is the PRIMARY
  enforcement path, not this bullet. This branch is a belt-and-suspenders assertion
  of the invariant "never `done` on an unran gate". Do NOT delete the First-action
  `awaiting-db-gates` bullet believing P7 catches this case; keep the two in sync if
  you change the dispatch order.
- `CLOSED` (abandoned, not merged) **and** `db_gate.applied_ahead_of_merge` → LOUD
  halt: the apply-now migration is now orphaned live in prod. Surface that the paired
  rollback (`db_gate.rollback`) MUST be run, and require explicit human confirmation
  it was run (or a deliberate waiver) before setting `status:"done"`. Do NOT silently
  close — this is the only prompt that re-surfaces the rollback obligation once the
  PR is gone (the handoff markdown alone re-surfaces nothing). This P7 branch catches
  a PR that reached P7 with a clean/applied gate and was THEN closed; a PR abandoned
  earlier — while the gate was still held at `awaiting-db-gates` (apply-now with
  pending deploys) — never reaches P7, so First action runs this identical CLOSED
  check. Keep the two CLOSED handlers in sync.
- `CLOSED` (abandoned, not merged) **and** no applied-ahead migration → nothing is
  orphaned; report the pipeline was abandoned, set `status:"done"`, and stop.
- `OPEN` → still awaiting merge; re-report the PR URL + status and stop.

## Failure handling

- Any failure → `status:"blocked"` + blocker text + halt + report. Never proceed
  dirty, never loop forever.
- A re-invoked `blocked` pipeline surfaces the blocker and waits for the human; it
  never silently re-runs or skips the failed phase.
- Branch mismatch (state `branch` ≠ current branch) → warn + reconcile + stop.
- `awaiting-db-gates` (P6.5) is a deliberate LOUD halt, NOT a failure: the DB gate
  was deferred and the pipeline waits for explicit human ack (see First action). It
  never auto-closes on a merged PR without that ack. (A CLOSED/abandoned PR is
  different: First action reports abandonment — and, if a migration was applied ahead
  of merge, LOUDLY demands the paired rollback — instead of acking a deploy.)
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is never modified by this skill.
