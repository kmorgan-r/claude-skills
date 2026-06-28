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

Preconditions (any failure → stop and ask, do NOT branch):
- `git status --porcelain` empty (clean tree).
- `git fetch` then ensure local `main` is current.
- Local branch absent: `git branch --list feat/<slug>` empty.
- **Remote** branch absent: `git ls-remote --heads origin feat/<slug>` empty
  (avoids a later push collision).

Action:
```bash
git checkout main && git pull --ff-only
git checkout -b feat/<slug>
# Per-run scratch state files must never be tracked (else they cause merge
# conflicts on shared repos and can leak blocker text). Ensure both are ignored:
grep -qxF '.claude-ship-state.json' .gitignore 2>/dev/null || echo '.claude-ship-state.json' >> .gitignore
grep -qxF '.claude-pr-fix-state.json' .gitignore 2>/dev/null || echo '.claude-pr-fix-state.json' >> .gitignore
git add .gitignore && git commit -m "chore: ignore ship/fix-pr-reviews state files"
```
Then write the initial state file (`phase:"spec-review"`, `branch`, `spec`,
`topic`, `focus_next`). **Rollback:** if the state-file write fails after the
branch was created, run `git checkout main && git branch -D feat/<slug>`.
Advance to P1.

### P1 spec-review

Invoke `reviewing-plans` via the Skill tool **with the `auto` argument**, pointed
at the design doc (`spec`) — invoke reviewing-plans auto mode via args
`auto <spec-path>`. Auto mode applies ALL findings without pausing and returns a
summary (see the reviewing-plans auto contract). After it returns, run it once
more in `auto` mode (re-review). If any **unresolved CRITICAL** finding remains
after auto-apply → set `status:"blocked"`, append it to `blockers`, stop.
Also block if reviewing-plans returns a **total reviewer failure** (a
`REVIEW FAILED` summary / zero reviewers succeeded) — a zero-findings result
from total failure is NOT a clean review. Otherwise update state
(`phase:"writing-plans"`) and advance.

### P2 writing-plans

If `plan` is already set OR a plan file for `<slug>` already exists in
`docs/superpowers/plans/` (brainstorming may have auto-chained writing-plans),
**skip creation** and record the existing path. Otherwise invoke `writing-plans`
via the Skill tool; ignore any auto-chain into execution. Record the plan path in
`plan`. Advance to P3.

### P3 plan-review

Invoke `reviewing-plans` via the Skill tool in auto mode via `auto <plan-path>` arguments.
Re-review once in auto mode. Unresolved CRITICAL after auto-apply →
`status:"blocked"` + stop. A total reviewer failure (`REVIEW FAILED` / zero
reviewers succeeded) → `status:"blocked"` + stop. Otherwise advance to P4.

### P4 implementation

Invoke `subagent-driven-development` via the Skill tool on the `plan` (its Task
subagents keep your context lean; subagents self-verify per task via TDD). When
it completes, run the **exit gate** — NOT the full test suite:
```bash
npm run lint && npm run check:types
```
then run ONLY the change's own test files. **First populate `test_paths`** in the
state file (so a later resume never re-infers them): collect the test files this
branch added or changed —
```bash
git diff --name-only $(git merge-base main HEAD)..HEAD -- '*.test.*' '*.spec.*'
```
(Git pathspec wildcards match across `/` — unlike shell globs — so these patterns
DO catch nested files like `src/**/foo.test.ts`; verified against this repo.)
Write that list into state `test_paths`, then gate on exactly those:
```bash
npx vitest run <the test_paths list>
```
If `test_paths` is empty (the change added no tests), the gate is lint +
check:types only — **never** fall back to a full `npm test`/`vitest run` (the
repo's ~70 pre-existing failures would block every pipeline). On resume, read
`test_paths` from state rather than re-deriving it.
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
- **Fresh entry** (no `.claude-pr-fix-state.json`, or it is for a different PR) →
  invoke `fix-pr-reviews --loop`.
- **Resume into P6** (a `.claude-pr-fix-state.json` for THIS PR exists) → invoke
  `fix-pr-reviews --loop --continue` (preserves its counter, not restarted at 1).

Determine the outcome by reading fix-pr-reviews' final output block AND its state
file, then map (no silent fall-through):

| fix-pr-reviews outcome | how to detect | ship outcome |
|------------------------|---------------|--------------|
| all-clear | `## Loop Complete - All Clear!` OR `## Loop Complete - No Urgent Issues` | advance to **P7** |
| max-iterations, issues remain | `## Loop Stopped - Max Iterations Reached` | `status:"blocked"` |
| all-remaining-issues skipped | `## Loop Complete — Human Review Needed` | `status:"blocked"` |
| unparseable review / workflow fail | its `URGENT_TOTAL=-1` stop | `status:"blocked"` |
| unrecognized output | none of the above patterns match | `status:"blocked"`, surface the raw fix-pr-reviews output verbatim in `blockers` |

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
