---
name: ship-fleet
description: Use when shipping a batch of GitHub issues in parallel - spawns headless Claude Code instances each running the full /ship pipeline in its own git worktree, with crash recovery, monitoring, and human-gate surfacing
---

# ship-fleet — Parallel Ship Pipeline Dispatcher

Runs up to N (default 10) `/ship` pipelines concurrently: one headless
Claude Code instance (`claude -p`) per GitHub issue, each in its own git
worktree, coordinated through a fleet manifest and a polling monitor.
Ship itself is never modified — fleet consumes only ship's documented
state-file contract (`.claude-ship-state.json` schema + First-action
resume semantics). Dependency is one-way: ship never knows fleet exists.

> This is a Claude Code skill (an instruction set Claude follows at
> runtime). The invoking session does resolution, worktree setup, state
> seeding, process spawning, and monitoring inline. There are no helper
> scripts to keep in sync — this file is the whole artifact.

## Hard rules

1. **Never merge a PR.** Merge is always the human's.
2. **Never ack a DB gate.** Ship P6.5 / `awaiting-db-gates` is a human-only
   production-write gate. Fleet surfaces the checklist; it never confirms it.
3. **Never answer ship's questions.** A headless instance that stopped on a
   question is surfaced to the human (`halted` + notification), not answered
   by the monitor.
4. **Single writer.** At most one session writes the manifest at a time —
   enforced via `monitor_heartbeat` (see Fleet manifest). Everything else is
   report-only.
5. Every deliberate instance termination is a tree-kill:
   `taskkill /PID <pid> /T /F`. Killing only the `pwsh` wrapper orphans the
   `claude` child, which keeps writing to the worktree.

## Compact instructions

> Preserved during auto-compaction. After ANY compaction, immediately:
> 1. Read `.claude-fleet-state.json` at the main repo root.
> 2. If you were the monitor (your `monitor_pid` is in the manifest and the
>    heartbeat is yours), resume the Monitor loop at the next tick.
> 3. If you were mid-setup (instances still `queued`, no monitor running),
>    resume Per-instance setup for the first instance lacking a `pid` —
>    but only while liveness-confirmed running instances < `max_concurrent`
>    (queued-beyond-max instances deliberately lack pids; do not over-spawn
>    past the cap).
> 4. Preserve: `fleet_id`, `default_branch`, `max_concurrent`, every
>    instance's `worktree`/`branch`/`bootstrap`/`plan_path`/`spec_path`/
>    `restarts`.

## First action (EVERY invoke)

Read `.claude-fleet-state.json` at the repo root (absolute paths inside it):

- **Absent** → no fleet exists. `status`/`resume`/`cleanup` → report "no
  fleet manifest" and stop. Issue arguments → start a new fleet (Fleet
  setup below).
- **Present, `monitor_heartbeat` fresh** (younger than 2× the poll
  interval, i.e. < 10 minutes old) → a live monitor owns the manifest.
  EVERY invocation is report-only: print the dashboard + "needs you" list;
  for `resume`/new-fleet requests, explain the live monitor owns the fleet
  and what the operator can do (wait, or kill the monitor session first).
  Never write the manifest, never spawn.
- **Present, heartbeat stale, some instances non-terminal by
  `fleet_status`** (`queued`, `running`, or `crashed` — `fleet_status` is
  the authoritative criterion here; a `halted` instance whose ship state is
  still in-progress is TERMINAL for this dispatch and belongs to the next
  branch) → the monitor died. Behave as `status`, then offer `resume`. A
  request to start a NEW fleet is refused until this one is terminal and
  cleaned up.
- **Present, all instances terminal by `fleet_status`** (`awaiting-merge`/
  `halted`/`done`/`wedged`/`skipped`) → fleet finished but may not be
  cleaned up. `status` → dashboard. `resume` → see Subcommands (its duty 1
  legitimately respawns `halted`/`wedged` instances the human has
  unblocked). `cleanup` → Cleanup below. New-fleet request → allowed
  ONLY if every instance is `done` or `skipped` AND its worktree is
  removed; otherwise instruct the operator to run `/ship-fleet cleanup`
  first. Before starting the new fleet, archive the old manifest to
  `<worktrees-root>\archive\<fleet_id>.json` (create the dir if needed).

## Argument grammar

`/ship-fleet <args>` where `<args>` is one of:

| Form | Meaning |
|------|---------|
| `1032-1039` | inclusive issue range |
| `1032 1035 1038` | explicit issue list (space-separated) |
| `…` + `--max N` | concurrency cap (default 10) |
| `…` + `--dry-run` | resolution table + every planned command; ZERO side effects (no fetch-writes, no worktrees, no branches, no spawns, no manifest) |
| `status` | read-only dashboard + "needs you" list |
| `resume` | respawn unblocked/queued instances, restart monitor (subject to single-writer rule) |
| `cleanup` | remove worktrees/branches for done/merged; wedged with confirmation |

Range and list may not be combined with a subcommand. Unparseable args →
show this table and stop.

## Fleet setup

Runs once per new fleet, before anything is created. Any failure → stop,
nothing created.

1. **Preconditions:** `claude` CLI on PATH (`Get-Command claude`),
   `gh auth status` exits 0, `git remote get-url origin` exits 0.
2. **Fetch + default branch:**
   ```bash
   git fetch origin
   DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null \
     || git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
   [ -z "$DEFAULT_BRANCH" ] && { echo "ERROR: could not derive default branch"; exit 1; }
   ```
3. **Fast-forward the LOCAL default branch.** Ship P4 (`git merge-base
   "$DEFAULT_BRANCH" HEAD`) and P6.5 (DB diffs vs merge-base) resolve the
   local ref; `git fetch` alone does not move it, and a stale local default
   pollutes every instance's `test_paths` and false-triggers DB gates.
   - Not checked out in any worktree (`git worktree list` shows no tree on
     it): `git fetch origin "$DEFAULT_BRANCH:$DEFAULT_BRANCH"`.
   - Checked out in the primary tree and fast-forwardable:
     `git -C <primary-tree> pull --ff-only origin "$DEFAULT_BRANCH"`.
   - Otherwise (diverged / conflicting local changes) → STOP: tell the
     human to reconcile. Nothing is created before this passes.
4. **Gitignore hygiene, two layers:**
   - Primary tree, uncommitted: ensure `.git/info/exclude` contains
     `.claude-fleet-state.json` (append if missing — the manifest lives at
     the main repo root and a committed ignore entry only reaches the
     default branch after some fleet PR merges).
   - Per feature branch, committed: handled in Per-instance setup step 2.
5. **Worktrees root:** `<worktrees-root>` = sibling directory
   `..\ship-fleet\<primary-tree-dirname>` where `<primary-tree-dirname>`
   is the **directory basename of the primary working tree** (e.g.
   `climatepoint-eco-report-backend3`), NOT the origin repo name — sibling
   clones (`…backend2`, `…backend3`) must not collide in one shared folder.
   Create it (and `archive\` under it) if absent. Record the ABSOLUTE path.
6. **Initial manifest write — BEFORE any worktree or spawn.** After
   resolution, write `.claude-fleet-state.json` with every resolved
   instance at `fleet_status:"queued"` (`pid:null`, `spawned_at:null`,
   `plan_path`/`spec_path` from resolution) and every skip recorded
   (`fleet_status:"skipped"` + reason). **This first write claims the
   writer lock:** stamp `monitor_pid` (this session) and a fresh
   `monitor_heartbeat` (UTC now), and restamp the heartbeat on every
   subsequent manifest write during setup (each pid/spawned_at recording) —
   otherwise the whole multi-minute setup window reads as
   "stale heartbeat + non-terminal instances" to a concurrent invoke, whose
   `resume` would double-spawn mid-setup. A session crash mid-setup must
   leave nothing invisible to `status`/`resume`/`cleanup` — the manifest
   exists before the first side effect, and the Compact instructions'
   mid-setup recovery path depends on it.

## Issue resolution (per issue N)

For each requested issue N, resolve against **origin/<default-branch>**
(never the local tree — local uncommitted plans/specs are invisible to a
worktree branched from origin):

1. `gh issue view N --json state,title,url` → closed → **skip**
   (`skip_reason: "issue closed"`). Title is bare-mode slug material.
2. **Plan?** `git ls-tree -r --name-only "origin/$DEFAULT_BRANCH" -- docs/superpowers/plans/ | grep -E -- "-issue-N-[^/]*\.md$"`
   — note the anchored trailing hyphen: `issue-103-` must NOT match
   `issue-1032-…`. Exactly one hit → `mode: plan`, `plan_path` = the hit.
   Multiple hits → ambiguous → **skip** listing candidates.
3. **Spec match** — runs for EVERY issue, plan or no plan. Two-step:
   - Filename: `git ls-tree -r --name-only "origin/$DEFAULT_BRANCH" -- docs/superpowers/specs/ | grep -E -- "-issue-N-[^/]*-design\.md$"`
   - Content: `git grep -l -E "(#N\b|issues/N\b)" "origin/$DEFAULT_BRANCH" -- docs/superpowers/specs/ | sed 's/^[^:]*://'`
     — `git grep -l` against a ref prefixes every hit with `origin/main:`;
     the `sed` strips it. Without the strip, a spec matching BOTH steps
     (the common case — issue-numbered specs also contain `#N` in the
     body) appears as two distinct strings and a real single match reads
     as ambiguous, and a seeded `origin/main:docs/…` path is unreadable in
     the worktree.
   Union of both, deduped on the bare repo-relative path. Semantics depend
   on mode:
   - **No plan found (step 2 empty):** exactly one match → `mode: spec`,
     `spec_path` = the match, echoed at spawn time (not only in dry-run) so
     the operator can catch a wrong hit. More than one → ambiguous →
     **skip** listing candidates (operator picks and re-runs, or
     deliberately lets it go bare by deleting the stale references). Zero →
     `mode: bare`.
   - **Plan found (`mode: plan`):** exactly one match → set `spec_path`
     too (ship's handoff and any spec-reading phase see it). Zero or
     multiple → `spec_path: null` deliberately — no skip; the plan is the
     driving artifact and ambiguity here is harmless.
4. **Slug:** apply ship P0's normalization (lowercase; spaces/underscores →
   hyphens; strip chars outside `[a-z0-9-]`; collapse repeated hyphens) to:
   - plan/spec mode: the artifact filename minus `YYYY-MM-DD-` prefix and
     `.md` (and `-design` suffix stays — it is part of spec-derived slugs
     only when present in the filename; plan-derived slugs have none),
   - bare mode: the issue title, then **prefix the result with `issue-N-`**
     (bare branches sort with plan-derived ones like `feat/issue-1032-…`).
   Validate: `git check-ref-format --branch "feat/<slug>"`. Empty/invalid →
   **skip** (`skip_reason: "invalid slug"`).
5. **Collision checks:** `git branch --list "feat/<slug>"` non-empty, or
   `git ls-remote --heads origin "feat/<slug>"` non-empty → **skip**
   (`skip_reason: "branch exists"`). Open PR referencing the issue —
   `gh pr list --state open --json number,title,body,headRefName` and match
   `#N\b` in title/body or `issue-N-` in headRefName → **skip**
   (`skip_reason: "open PR #<num>"`).
6. **Bare-mode dictated spec path:**
   `docs/superpowers/specs/<today>-<slug>-design.md` — the bare slug
   already carries `issue-N-`; do NOT add another prefix (that would double
   it: `…-issue-1040-issue-1040-…`).

**Skips never abort the fleet.** Each skip carries its remedy into the
final report — e.g. for a stale leftover branch with no open PR:
`git push origin --delete feat/<slug>` + `git branch -D feat/<slug>`, then
re-run; or finish the existing branch manually.

**Unmerged-plans trap:** because resolution reads origin/<default>, plans
sitting in an unmerged PR are invisible and their issues silently go
`bare` (duplicate specs). Check open PRs touching plans:
`gh pr list --state open --json number,files --jq '.[] | select([.files[].path] | any(startswith("docs/superpowers/plans/"))) | .number'`
— if any returned PR's files match a requested issue's `issue-N-` pattern,
warn "merge PR #<num> first" and mark the issue skipped unless the operator
proceeds deliberately.

**Dry-run** prints the full resolution table — `issue | mode | slug |
artifact | skip_reason` — plus every command that WOULD run (worktree adds,
seed-file contents, spawn lines) and stops. Zero side effects: no worktrees,
no branches, no bootstrap files, no manifest, no spawns.

<!-- Task 3 -->
## Per-instance setup

<!-- Task 3 continues -->
## Spawn

<!-- Task 4 -->
## Fleet manifest

<!-- Task 4 continues -->
## Monitor loop

<!-- Task 5 -->
## Subcommands: status / resume / cleanup

<!-- Task 5 continues -->
## Edge cases

<!-- Task 5 continues -->
## Coupling contract

<!-- Task 6 -->
## First-run validation
