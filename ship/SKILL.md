---
name: ship
description: Conductor that drives the post-brainstorm dev pipeline hands-off — spec-review, plan, plan-review, implementation, PR, fix-pr-reviews loop — stopping only on failure or final merge. Use after /superpowers:brainstorming when a committed spec exists, or to resume an in-progress pipeline.
---

# Ship — Pipeline Conductor

Drives the mechanical tail of development after brainstorming. Invoked once;
runs phases P0–P7 hands-off, resuming after any `/clear` or auto-compact via a
state file.

> This is a Claude Code skill (an instruction set Claude follows at runtime).
> **Authority over control flow:** when a delegated skill ends with a hand-off
> prompt (e.g. reviewing-plans' "ready to execute?", brainstorming auto-invoking
> writing-plans), DO NOT stop or follow it — continue to the next phase as
> defined here. You (the conductor) own the sequence.

## Compact Instructions

> Preserved during auto-compaction. After ANY compaction, immediately:
> 1. Read `.claude-ship-state.json` (repo root).
> 2. Resume at `phase` using `focus_next`.
> 3. Preserve: `topic`, `branch`, `phase`, `status`, `pr`, `plan`, `blockers`.
> If `phase == "fix-pr-reviews"`, the loop internals belong to fix-pr-reviews
> (`.claude-pr-fix-state.json`) — defer to it; re-enter with `--loop --continue`.

## First action (EVERY invoke)

Read `.claude-ship-state.json`:

- **Present and `status == "done"`** → report "pipeline already complete for
  <topic>" and stop.
- **Present and `status == "blocked"`** → surface the blocker(s) verbatim and ask
  the user to clear them. Do NOT silently re-run or skip the failed phase.
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
  "test_paths": []
}
```

`test_paths` is the explicit list of test files the P4 gate runs (see P4). It is
populated during P4 and persists so a post-compaction resume never has to
re-infer it (and therefore never falls back to the full, always-failing suite).

Rewrite it at every phase boundary (update `phase`, `focus_next`, append to
`phase_log`). On a failure set `status:"blocked"` and append to `blockers`.

## Phases

Each phase: check preconditions → run the action (invoke the named skill via the
Skill tool, overriding its hand-off) → write state (Write tool) → advance or block.

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

Invoke `reviewing-plans` via the Skill tool **with the `auto` argument**, pointed
at the design doc (`spec`) — invoke reviewing-plans auto mode via args
`auto <spec-path>`. Auto mode applies ALL findings without pausing and returns a
summary (see the reviewing-plans auto contract). After it returns, run it once
more in `auto` mode (re-review). If any **unresolved CRITICAL** finding remains
after auto-apply → set `status:"blocked"`, append it to `blockers`, stop.
Also block on reviewer-coverage failure, read from the `REVIEWERS: X/N succeeded
(failed: …)` line reviewing-plans emits in its auto summary:
- **Total failure** (`REVIEW FAILED` / `0/N`) — a zero-findings result from total
  failure is NOT a clean review.
- **Partial failure that guts coverage** — block if fewer than 2 reviewers
  succeeded, OR fewer than half of those dispatched succeeded, OR either always-on
  reviewer (General Quality / Test Quality) is in the failed list. A hands-off
  conductor cannot judge which silently-failed domain mattered, so an
  under-covered review is treated as no review, not a clean one.
Otherwise update state (`phase:"writing-plans"`) and advance.

### P2 writing-plans

If `plan` is already set OR a plan file for `<slug>` already exists in
`docs/superpowers/plans/` (brainstorming may have auto-chained writing-plans),
**skip creation** and record the existing path. Otherwise invoke `writing-plans`
via the Skill tool; ignore any auto-chain into execution. Record the plan path in
`plan`. Advance to P3.

### P3 plan-review

Invoke `reviewing-plans` via the Skill tool in auto mode via `auto <plan-path>` arguments.
Re-review once in auto mode. Unresolved CRITICAL after auto-apply →
`status:"blocked"` + stop. Apply the **same reviewer-coverage gate as P1** (read
`REVIEWERS: X/N succeeded`): block on total failure, on fewer than 2 succeeding,
on fewer than half of those dispatched succeeding, or on either always-on reviewer
failing. Otherwise advance to P4.

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
| all-clear | `Loop Complete` AND (`All Clear` OR `No Urgent Issues`) | advance to **P7** |
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

### P7 awaiting-merge

First `mkdir -p docs/superpowers/handoffs/` (the Write tool does not create
parent directories, and this dir may not exist on a repo that has never
completed a `ship` run). Then write the one-time committed summary to
`docs/superpowers/handoffs/ship-<slug>.md` (topic, branch, PR URL, final review
status, `phase_log`, leftovers) and commit it. Report the PR URL + status. **STOP — the human merges manually; the conductor
never merges.** On a later `/ship` invoke, check `gh pr view <pr> --json state`;
if `MERGED` → set `status:"done"` and stop.

## Failure handling

- Any failure → `status:"blocked"` + blocker text + halt + report. Never proceed
  dirty, never loop forever.
- A re-invoked `blocked` pipeline surfaces the blocker and waits for the human; it
  never silently re-runs or skips the failed phase.
- Branch mismatch (state `branch` ≠ current branch) → warn + reconcile + stop.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is never modified by this skill.
