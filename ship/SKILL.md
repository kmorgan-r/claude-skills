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
> 3. Preserve: `topic`, `branch`, `phase`, `status`, `pr`, `plan`, `blockers`, `db_gate`, `review_passes`, `repair`, `repair_enabled`.
> If `phase == "fix-pr-reviews"`, the loop internals belong to fix-pr-reviews
> (`.claude-pr-fix-state.json`) — defer to it; re-enter with `--loop --continue`.
> If `status == "awaiting-db-gates"`, the P6.5 DB gate was deferred — surface
> `db_gate.checklist`, require explicit human ack that the gate ran, and do NOT
> close out on a MERGED PR alone (see First action).
> If `repair.in_flight` is true, a repair was interrupted — reconcile it (see First
> action) BEFORE resuming at `phase`; `focus_next` still points at pre-repair work.
> A mid-phase clarifying question from a delegated skill is a Class-B stop, not a
> halt — answer or escalate it per Class-B stops; never idle on one.

## First action (EVERY invoke)

Read `.claude-ship-state.json`:

- **Present and `status == "done"`** → report "pipeline already complete for
  <topic>" and stop.
- **Present and `status == "blocked"`** → surface the blocker(s) verbatim and ask
  the user to clear them. Do NOT silently re-run or skip the failed phase.
  If the human explicitly confirms the blocker is cleared, **set `repair` back to
  `null`** — which the schema defines as `attempt: 0`, `budget_used: 0`, `history: []` —
  log the reset to `phase_log`, **clear `blockers`** (the reconciliation exits above append
  paths there, and this branch surfaces them verbatim, so a stale entry would resurface
  beside the next unrelated block), and return `status` to `in-progress`. Human intervention
  earns fresh retries. Reset the whole block, not just `budget_used` and `history`: a cap
  block leaves `attempt` at 2 and `phase` unchanged, so a partial reset would find the
  same phase on re-entry, perform no `attempt` reset, and block again having dispatched
  nothing. The reset must hang on
  that explicit confirmation and nothing else: this branch is a dead end and there is
  no `/ship resume` verb, so on a later re-invoke ship sees an ordinary `in-progress`
  state and cannot detect the blocked→cleared transition at all. After the reset,
  continue at the handler for `phase` in this same invoke — do not reset and stop.
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
  about the mismatch, ask the user to reconcile, and stop. Otherwise, if
  `repair.in_flight` is true, a repair was interrupted — reconcile BEFORE jumping
  to the phase handler, in this order:
  1. `git status --porcelain`. Empty → the tree is clean, which does NOT by itself
     mean nothing ran: `ship-repair` commits an `applied` repair BEFORE clearing
     `in_flight`. Check `git log -1 --format=%s` against
     `fix: repair <repair.phase> gate failure (attempt <repair.attempt>)`. Match —
     the attempt ran and landed: clear `repair.in_flight`, append its `history` entry
     with verdict `applied` **and a copy of `repair.on_resolved`** — this append is the
     one the P1/P3 hooks never reached, so dropping the copy strands the resume branch
     below with nothing to route on — log
     `repair <phase> attempt N → applied (reconciled)`
     to `phase_log`, do NOT decrement, then **skip steps 2 and 3** and continue below;
     the phase handler judges the result: at P4 the whole-gate re-run, at P1/P3 the
     pending-verification branch in their preambles, which runs the read-only
     `RESOLVED:` verifier this repair never reached. Jumping to the handler top is
     therefore right at every phase — but only because those preambles exist. Without
     them P1/P3 would dispatch a fresh applying panel instead, burning a
     `review_passes` slot against the two-pass ceiling and never asking whether the
     repair resolved the CRITICAL it was dispatched for.
     Falling through to step 3
     would decrement a landed attempt and re-open the over-cap path this branch
     exists to close.
     No match — nothing landed; skip to 3. Non-empty →
     `git checkout HEAD -- <repair.touched_paths>`, then — **only when
     `created_paths` is NON-EMPTY** — `git clean -f -- <repair.created_paths>`.
     With an empty list that second command becomes the literal `git clean -f --`,
     and a `--` carrying zero pathspecs is not "match nothing": git reads it as
     no path restriction, so it runs as a bare `git clean -f` and removes
     untracked files across the repository that no repair ever touched.
     `created_paths` is routinely `[]`, so the guard fires on the ordinary path,
     not an edge case — and it hides nothing, because step 2 below still catches
     whatever the agent created outside its manifest. If either key is ABSENT
     from state (an empty list is not absent), do NOT guess → append the dirty
     paths to `blockers`, `status:"blocked"`, stop.
  2. `git status --porcelain` again. Still non-empty → the agent wrote outside its
     manifest; append the residual paths to `blockers`, `status:"blocked"`, stop.
     Catching it here names the paths; leaving it to `ship-repair`'s dirty-tree
     precondition returns `refused`, which blocks with no retry.
  3. Clear `repair.in_flight` and **decrement `repair.attempt` by 1** — it was
     incremented at dispatch, and leaving it counts an attempt that never ran,
     silently halving the cap. Leave `budget_used` charged; the dispatch happened.
  One window stays undetectable by design: a `failed` verdict interrupted after
  `ship-repair`'s own revert leaves a clean tree and no commit, indistinguishable from
  never-ran. Its cost is bounded — one extra budget-charged retry under identical
  conditions, with nothing committed to compound.
  Do NOT re-run the phase gate to refresh the signature: the revert restores the
  tree `ship-repair` saw at dispatch, so `repair.failure` and `repair.signature`
  still describe it, and at P1/P3 the only such gate is a `reviewing-plans`
  dispatch that would burn a slot against the two-pass ceiling.
  Then jump to the handler for `phase` (see Phases). If a
  non-done state already exists and the user names a DIFFERENT spec, warn (one
  active pipeline only) and ask before overwriting.
- **Absent + a committed spec exists** in `docs/superpowers/specs/` → confirm
  which spec to use (default: most recent; otherwise ask), then start at **P0**.
- **Absent + no spec** → offer to run `/superpowers:brainstorming` first.

`/ship --no-repair` sets `repair_enabled: false` in state on the invocation carrying
it, and it persists there — repair stays off across compaction, `/clear`, and fleet
respawn until explicitly re-enabled. Limit: `ship-fleet` spawns from a stored
`bootstrap` string and is not edited by this design, so disabling repair fleet-wide
means editing that stored bootstrap, not passing a flag.

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
  "review_passes": { "spec-review": 0, "plan-review": 0 },
  "repair_enabled": true,
  "repair": null
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

`repair` records in-flight and historical repair state (`null` until a repair runs).
A `null` block reads as `attempt: 0`, `budget_used: 0`, `history: []`. Shape:

    "repair": {
      "phase": "implementation",
      "attempt": 1,
      "signature": "check:types|src/foo.ts:14|ts2345",
      "failure": "<verbatim failing output, truncated to 8KB>",
      "budget_used": 1,
      "in_flight": true,
      "touched_paths": ["src/foo.ts"],
      "created_paths": [],
      "on_resolved": "step3",
      "history": [ {"phase": "plan-review", "attempt": 1, "signature": "...", "verdict": "applied",
                    "on_resolved": "step3", "resolved": "yes"} ]
    }

`attempt` counts attempts at the CURRENT halt point and resets on a change of
`repair.phase` and on nothing else — a signature-based reset makes the cap of 2
unreachable and deadlocks it against the ratchet. `budget_used` counts agent
dispatches across the whole pipeline and never resets on its own. `touched_paths`
and `created_paths` are written by `ship-repair` BEFORE the agent is dispatched,
because an interrupted repair writes nothing afterwards and the resume path needs
them to reconcile the tree. `in_flight` is cleared on every terminal verdict.
`on_resolved` is written at dispatch by the P1/P3 hooks — `"step3"` by the first,
`"advance"` by the second — and records what a later `RESOLVED: yes` is supposed to
mean. P4 leaves it unset. EVERY site that appends a `history` entry copies it onto that
entry, First action's reconciliation included, and the P1/P3 preambles' pending-
verification branch reads it back from there to route a repair whose verification was
interrupted. `resolved` is written onto the same entry when the verifier answers. Both
keys are inert to the ratchet, which compares `signature` only.

`repair_enabled` is the `--no-repair` kill switch (default `true`). It lives in
state rather than only in the invocation, because an argument does not survive an
auto-compaction, a `/clear`, or a fleet respawn — and surviving those is what this
state file exists for.

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

If a delegated skill asks a clarifying question mid-phase, ship does NOT idle waiting
on it — see **Class-B stops**. An idle ship records no blocker, so nothing surfaces the
stall to anyone, and it is a stop before P5.

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

**Before anything else — before the ceiling check — settle a landed repair that was
never verified.** If the most recent `repair.history` entry whose `phase` is
`spec-review` carries `verdict: "applied"`, a repair for this phase landed and the
read-only `RESOLVED:` verifier that judges it may never have run. Do not dispatch a
panel until this is settled:

- **No `resolved` key** → the verifier never returned. Run it now, exactly as step 2
  below specifies it: ONE report-only Opus reviewer via the Agent tool, given
  `repair.failure` and the repair commit's diff (`git show` the
  `fix: repair spec-review gate failure (attempt N)` commit — after a compaction that
  diff exists nowhere else). It is READ-ONLY and does NOT increment `review_passes`.
  Write its answer to `resolved` on that entry before acting on it.
- **`resolved: "no"`** → return to the repair-dispatch decision, exactly as steps 2
  and 5 do.
- **`resolved: "yes"`** → do what that entry's `on_resolved` says, written at dispatch
  by whichever hook dispatched: `"step3"` → re-enter the pass sequence at step 3,
  forcing **at least** row e, reading pass 1's `REVIEWERS:` and `FINDINGS:` lines back
  from `phase_log`, where step 2 persisted them for exactly this. `"advance"` →
  advance the phase. `on_resolved` ABSENT → `status:"blocked"`; do not guess which hook
  ran, and do not fall through to a panel dispatch.

Route on `on_resolved`, never on `review_passes`: the count moves between recording
`resolved` and acting on it, because a `"step3"` resume dispatches `--diff` and
increments it — so a counter-based rule reads a finished first-hook repair as a
second-hook one and would advance the phase on a `--diff` result nobody read. If that
`--diff` is itself lost to a second compaction, this branch re-enters step 3, row e
forces a dispatch, and step 4's ceiling check blocks at `review_passes >= 2`. That
block is correct rather than a regression: two applying passes were dispatched and the
second's outcome is unreadable, and ship never advances on a signal it cannot read.

This branch sits ABOVE the ceiling check deliberately. A repair dispatched from step 5
lands with `review_passes` already at 2, so reading the ceiling first would block the
only route that reaches its verification — spending the repair, the budget unit and the
interruption, then discarding the answer they bought. Firing this branch when it was not
strictly needed costs one read-only dispatch and changes no state; skipping it costs a
`review_passes` slot burned on an unrelated full panel and a CRITICAL nobody re-read.

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
   downgraded_critical` (from the `FINDINGS:` line). `> 0` → attempt a repair first:
   run the repair-dispatch decision (see Repair), and if it allows, perform **every**
   state write that section's dispatch-and-verdict paragraph specifies — its
   "On dispatch" list AND its "On every returned verdict" `history` append, which the
   ratchet reads; it is the single source and this hook does not restate it — with
   `repair.phase` set to `spec-review` at P1 or
   `plan-review` at P3, and `repair.failure` to the verbatim unresolved-CRITICAL text.
   Two writes belong to THIS hook rather than to that generic list: `repair.on_resolved`
   = `"step3"`, and pass 1's verbatim `REVIEWERS:` and `FINDINGS:` lines appended to
   `phase_log` NOW rather than at the end of the phase. Both serve the resume path.
   `on_resolved` is what the preamble's pending-verification branch routes on, and it
   has to exist before the dispatch, because an interrupted repair is reconciled by
   First action, which cannot know which hook dispatched it. The two lines are step 3's
   only inputs: without them a resumed decision reads an absent `FINDINGS:`, takes
   row a, and silently deletes row d — a Tier 3 human halt — from the resumed path.
   Then invoke `ship-repair spec-review` (P1) or `ship-repair plan-review` (P3) via the
   Skill tool. On `REPAIR: applied`, verify with ONE report-only Opus reviewer,
   dispatched via the Agent tool with model `opus` and NOT `reviewing-plans` (whose
   `auto --diff` is also one Opus reviewer but APPLIES and commits), given the repair
   diff and the CRITICAL it was meant to resolve, instructed to edit nothing and
   answer one question. Its first line is machine-readable:
   `RESOLVED: yes — <why>` or `RESOLVED: no — <what still stands>`; an absent or
   unparseable line blocks, exactly as an unparseable `REPAIR:` line does. This
   verification dispatch is READ-ONLY and does NOT increment `review_passes`. Write the
   answer as `resolved: "yes"|"no"` onto that repair's `history` entry BEFORE acting on
   it — a compaction in the gap otherwise discards the one thing the dispatch bought,
   and the preamble branch re-runs the verifier to recover it.
   On `RESOLVED: yes`, **resume this pass sequence at step 3** and force **at least**
   decision row e — the repair edited the artifact, so pass 1's `FINDINGS:` counts no
   longer describe the file on disk. Rows a–d still take precedence and are still
   evaluated first: **row d still blocks**. Row d is a Tier 3 human halt, and forcing row e
   past it would run `--diff` over an empty apply set and return a clean `1/1` — precisely
   what row d exists to prevent. Rows b and c keep their `phase_log` obligations.
   On `RESOLVED: no`, return to the repair-dispatch
   decision: if it still allows a dispatch, attempt again — this is the 1→2 transition
   the cap of 2 bounds, and the only route to it at P1/P3. If the decision does not
   allow one, or on any other verdict → `status:"blocked"`,
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
   unresolved CRITICAL. On a coverage total failure → `status:"blocked"` + `blockers`,
   stop. On unresolved CRITICAL, this is the SECOND repair hook point:
   run the repair-dispatch decision and, if it allows, write the same state fields as
   step 2 — including, on the returned verdict, its "On every returned verdict"
   `history` append, which the ratchet reads, and the same `resolved` record — with two
   differences: `repair.on_resolved` is `"advance"` here, and step 2's `phase_log`
   persistence of pass 1's `REVIEWERS:`/`FINDINGS:` lines is left out, because this
   hook's resume never re-enters step 3 and so has no consumer for them — and invoke
   `ship-repair` for this
   phase, verifying with the same read-only `RESOLVED:` reviewer. It resumes
   DIFFERENTLY from step 2's: on `RESOLVED: yes` it
   **advances the phase**, and must NOT resume at step 3. By this point
   `review_passes` is already 2, so a forced row e would hit step 4's ceiling check
   and block — making `RESOLVED: yes` and `RESOLVED: no` produce the identical
   outcome after spending a repair dispatch, a budget unit and a verification
   dispatch to distinguish them. Resuming at step 3 is also circular: step 3 is the
   decision that produced the `--diff` pass that just ran. Advancing is defensible on
   the merits — two applying passes plus a read-only verification is more scrutiny
   than the normal path gives. On `RESOLVED: no`, follow the same return-to-decision
   rule as step 2. If the decision does not allow a further dispatch, or on any other
   verdict → `status:"blocked"` + `blockers`, stop. Else advance.
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

**Before anything else — before the ceiling check — settle a landed repair that was
never verified.** If the most recent `repair.history` entry whose `phase` is
`plan-review` carries `verdict: "applied"`, a repair for this phase landed and the
read-only `RESOLVED:` verifier that judges it may never have run. Do not dispatch a
panel until this is settled:

- **No `resolved` key** → run the verifier now, exactly as P1's step 2 specifies it:
  ONE report-only Opus reviewer via the Agent tool, given `repair.failure` and the
  repair commit's diff (`git show` the
  `fix: repair plan-review gate failure (attempt N)` commit). READ-ONLY; it does NOT
  increment `review_passes`. Write its answer to `resolved` on that entry first.
- **`resolved: "no"`** → return to the repair-dispatch decision.
- **`resolved: "yes"`** → do what that entry's `on_resolved` says: `"step3"` → re-enter
  the pass sequence at step 3, forcing **at least** row e, reading pass 1's `REVIEWERS:`
  and `FINDINGS:` lines back from `phase_log`; `"advance"` → advance the phase.
  `on_resolved` ABSENT → `status:"blocked"`; do not guess, and do not fall through to a
  panel dispatch.

Route on `on_resolved`, never on `review_passes` — the count moves between recording
`resolved` and acting on it. This branch is stated here in full for the same reason the
ceiling check below is: a conductor resumed into P3 executes P3, and the pass-sequence
reference further down carries neither. Its full rationale is P1's; the normative
content above is complete without it.

**Before any dispatch, read the ceiling.** If `review_passes["plan-review"] >= 2` →
`status:"blocked"`, blocker `P3 review ceiling reached (2 applying passes); resolve manually`,
stop. This is P1's preamble restated, NOT cross-referenced — it sits outside P1's numbered
pass sequence, so the "same pass sequence as P1 (steps 1–5)" reference below does not carry
it, and step 4's ceiling check guards only the `--diff` dispatch, never this first
full-panel one. A conductor resumed after compaction re-enters this phase at the top, and
without the check here it would dispatch a third pass unconditionally. A state file written
by a pre-change `/ship` has no `review_passes` key; read an absent key as `0`.

Then invoke `reviewing-plans` via the Skill tool with args `auto <plan-path>` — the
default 5-reviewer panel, NOT P1's `--max-reviewers 3` — and increment
`review_passes["plan-review"]` at dispatch. Plans are far larger than
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
Any failure → **attempt a repair before halting.** Run the repair-dispatch decision
(see Repair). If it allows: perform **every** state write the Repair section's
dispatch-and-verdict paragraph specifies — both its "On dispatch, ship writes ALL of
the following" list AND its "On every returned verdict" `history` append, which the
ratchet reads; that paragraph is the single source and this hook does not restate it
— with `repair.phase = "implementation"` and
`repair.failure` set to the verbatim failing output, truncated to 8KB. Then invoke
`ship-repair implementation` via the Skill tool. On
`REPAIR: applied`, re-run the WHOLE gate — `npm run lint`, `npm run check:types` and
`npx vitest run <test_paths>`, exactly as the gate ran them, NOT only the command that
failed — under the same
zero-verification guard as the original gate; a pass advances to P5. Re-running only the
failed command would advance on a repair that fixed `lint` and broke `check:types`.
On any other
verdict, or a failing re-run with no attempts left → `status:"blocked"`, write the
failing output summary to `blockers`, stop; a failing re-run WITH attempts left returns
to the repair-dispatch decision for the next attempt. If the decision does not allow a dispatch
→ `status:"blocked"` with its blocker text, stop. **The zero-verification guard above
is exempt: it is Tier 3 and blocks with no repair attempt.**
**P4-blocked resume:** re-invoking `/ship` resumes the failed task inside
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
| unparseable review / workflow fail | the `URGENT_TOTAL=-1` stop (detect per below) | Tier 1 retry, then `status:"blocked"` |
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

**The `URGENT_TOTAL=-1` row is the one Tier 1 retry ship owns.** Re-invoke
`fix-pr-reviews --loop --continue` exactly once before blocking, then re-read the
outcome against this table. This is a retry, not a repair: no agent, nothing edited,
no budget spend — the same operation runs again because a workflow `failure` or
`cancelled` is usually transient. Still unreadable on the second read → `blocked`.
**"Once" needs a durable marker, because neither skill counts this.** fix-pr-reviews
hard-stops on the `-1` sentinel WITHOUT advancing `iteration` or `total_rounds`, so its
own caps can never bound repeated retries; and a compaction mid-retry would otherwise
hand ship a fresh "once" on every resume. So: BEFORE retrying, scan `phase_log` for a
`p6 retry used` entry naming this PR — present → `blocked`, do not retry. Absent →
append `p6 retry used (pr <pr>)` to `phase_log` FIRST, then re-invoke. `phase_log` lives
in the state file on disk, so it survives what the retry itself does not.
This covers ONLY that row. `Max Iterations Reached`, `Human Review Needed`, and
unrecognized output remain Tier 3 and block on the first occurrence.

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

## Repair (P0–P6 only; never P6.5 or P7)

Dormant until a hook invokes it. On a repairable gate failure, ship attempts a
bounded repair before halting. Skip this section entirely when `repair_enabled`
is `false`.

### The repair-dispatch decision

Run BEFORE every dispatch. Any check failing means `status:"blocked"` — never a
dispatch, never a retry of the check. A `null` `repair` block reads as
`attempt: 0`, `budget_used: 0`, `history: []`.

1. **Tier check.** The halt must appear in the Tier 2 table below. Any halt not
   named in Tier 1 or Tier 2 is Tier 3 (human) by default.
2. **Kill switch.** `repair_enabled == false` → block as today.
3. **Per-halt-point cap.** If `repair.phase` differs from this halt's phase, reset
   `attempt` to 0. Then `attempt >= 2` → block with
   `repair cap reached (2 attempts at <phase>)`.
4. **Global budget.** `budget_used >= 5` → block with
   `repair budget exhausted (5/5) — <last failure>`.
5. **Same-signature ratchet.** Compute the normalized signature of the CURRENT
   failure and compare against every entry in `repair.history`. A match means the
   previous repair changed nothing that mattered → block immediately, no attempt,
   no budget spend.

**On dispatch, ship writes ALL of the following before invoking `ship-repair`:**
`repair.phase` = this halt's phase; `repair.attempt` = previous + 1; the computed
`repair.signature`; `repair.failure` = the verbatim failing output truncated to
8KB; `repair.in_flight` = `true`; and `budget_used` incremented. **`in_flight` is
set here and nowhere else** — `ship-repair` only ever clears it. Without this write
the resume reconciliation in First action can never fire, and an interrupted repair
strands its own working tree. **On every returned verdict**, ship appends
`{phase, attempt, signature, verdict}` to `repair.history` — **plus a copy of
`repair.on_resolved` whenever it is set**, which is every P1/P3 dispatch and no P4 one —
and logs
`repair <phase> attempt N → <verdict>` to `phase_log`. That copy is not optional
bookkeeping: the P1/P3 preambles' pending-verification branch routes on it, and this is
the append that runs on the finding's own path — `ship-repair` clears `in_flight` itself
on a terminal verdict, so a compaction after the append never reaches First action's
reconciliation, and an entry written without the marker sends the resumed repair to
`blocked` instead of to the verifier it was interrupted before. Without the `attempt`,
`signature` and `history` writes the cap is unreachable and the ratchet has
nothing to compare — both limits read fine in prose and never fire.

Signature normalization:

- **P4:** `<failing check name>|<first failing file>:<line>|<error code or first
  60 chars of the message>`, lowercased. Line numbers are kept — a genuine partial
  fix moves the error.
- **P3:** `<task number>|<first 80 chars of the CRITICAL finding Issue text>`,
  lowercased, whitespace collapsed.
- **P1:** the same, keyed on the spec SECTION HEADING the finding cites rather than
  a task number (a spec has no numbered tasks); `<no-section>` when it cites none.
- **P1/P3 additionally append** the verifier's `RESOLVED: no` explanation line
  (first 80 chars, lowercased). What this does and does not buy: attempt 1's
  signature has no verifier component, so the formats differ and the ratchet CANNOT
  fire at the 1→2 transition at P1/P3. There the cap of 2 is the binding limit on a
  zero-progress repair, costing one wasted dispatch pair. The verifier line still
  earns its place — without it the signature could not move even on real partial
  progress.

### Tier 1 — retry (no agent, exactly one re-run, no budget spend)

| Halt point | Retry action |
|---|---|
| P1/P3 **partial** reviewer-coverage failure | Owned by `reviewing-plans`, not ship. Ship's coverage gate reads a post-retry `REVIEWERS:` line and needs no change. |
| P6 `URGENT_TOTAL=-1` (unparseable review, workflow `failure`/`cancelled`) | Re-invoke `fix-pr-reviews --loop --continue` once, then re-read the outcome. Still unreadable → `blocked`. Implemented in the P6 outcome table and the retry rule beneath it. |

### Tier 2 — repair (external agent, capped)

| Halt point | Agent model | Cap |
|---|---|---|
| P4 exit gate: `lint`, `check:types`, or scoped `vitest run <test_paths>` fails | `sonnet` | 2 |
| P1/P3 unresolved CRITICAL after auto-apply | `fable` | 2 |

### Tier 3 — human, permanently

P6.5 DB gate; P7 merge; the P4 zero-verification guard (both cases); P6 `Max
Iterations Reached`; P6 `Human Review Needed`; P6 unrecognized output; P5
`pr-create` failure; P0 precondition failures and branch mismatch; P1/P3 TOTAL
reviewer failure (0/N); P1/P3 decision-table row d; P1/P3 review-ceiling blocks.

**Catch-all:** any halt not named in Tier 1 or Tier 2 is Tier 3. The list above
aims to be exhaustive and has been wrong before; the default is what makes an
omission safe rather than silent.

### Verdict handling

| Verdict | Response |
|---|---|
| `applied` | Re-run the phase gate. Pass → resume where the phase halted (per hook). Fail → next attempt if the decision allows, else `blocked`. |
| `failed` | Next attempt if the decision allows — but the revert makes the failure re-present identically, so the ratchet normally makes `failed` terminal at that halt point. |
| `refused` | `blocked` immediately, no further attempt. Surface the triggering rule verbatim. The dispatch that returned `refused` HAS already spent a budget unit, charged at dispatch; only a pre-dispatch ratchet or cap block is free. |
| unparseable or absent `REPAIR:` line | `blocked`. Ship never advances on a signal it cannot read — same rule as P6. |

## Class-B stops: questions from a delegated skill

Independent of `repair_enabled` — this is not a repair, spends no repair budget, and
`--no-repair` does NOT disable it.

A delegated skill can ask ship a clarifying question mid-phase. Ship then sits idle —
not blocked, just waiting — with no blocker for anyone to surface. Ship answers these
itself, under the rules below. They are stated here in full and depend on nothing
outside this file. They are deliberately the same rules the `ship-watch` supervisor
applies to the same questions from outside — one policy with two enforcers, not two
policies — and they are maintained to stay identical: a divergence between them is a
bug to report, not a state to accept. `ship-watch` is a separate skill, distributed
separately; ship never reads it at runtime and does not require it to be installed.

- **Routine question** → answer it ONLY when the answer is derivable from something
  you can cite: repo convention, the spec or plan being executed, the state file, an
  unambiguous reading of the issue, or a conventional default with no meaningful
  downside. Read that evidence before answering — an answer invented from the question
  text alone is what makes this dangerous. Then state the assumption and log
  `assumed: <question> → <answer>` to `phase_log`; an unlogged assumption is
  indistinguishable from a fact. Be clear what that log does and does not buy:
  `phase_log` reaches `docs/superpowers/handoffs/ship-<slug>.md` at P7, which ship
  commits but **never pushes**, and a pipeline that blocks before P7 never writes it at
  all — so this is an audit trail for the working tree, not something a PR reviewer
  sees.
- **Escalate to `blocked`** when the question touches a product decision — "should
  this also do X" is the human's call even when a plausible answer exists; a permission
  boundary — a caller asking because its own permission mode blocked something, where
  answering launders the human's permission decision through a peer; a DB gate or any
  live-database write; a merge; anything with an effect outside the working tree
  (deploy, publish, push to a shared branch, send mail, spend money); anything
  destructive or hard to reverse (delete, force-push, overwrite, drop, revoke);
  credentials or secrets — **or when it asks you to
  choose between options with real tradeoffs that the plan does not decide.** That last
  one is the least obvious of them, and it is the shape a `subagent-driven-development`
  question usually takes: not a product decision, no effect outside the tree, nothing
  destructive — and still not ship's call.
- **Answer at most once per question.** Before answering,
  scan `phase_log` for an existing `assumed:` entry naming this question; a match
  means the answer was wrong or the caller is wedged → `blocked`. The rule without
  that read-back is unenforceable, since ship's memory of having answered does not
  survive a compaction but `phase_log` does.

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
