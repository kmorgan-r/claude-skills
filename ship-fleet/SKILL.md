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

<!-- Task 2 -->
## Fleet setup

<!-- Task 2 continues -->
## Issue resolution (per issue N)

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
