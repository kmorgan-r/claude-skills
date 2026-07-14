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

## Per-instance setup

Replaces ship P0 entirely (P0's `git checkout <default>` cannot run in a
worktree while another tree has the branch checked out). In order:

1. **Worktree:**
   `git worktree add -b "feat/<slug>" "<worktrees-root>\<slug>" "origin/$DEFAULT_BRANCH"`
2. **Committed gitignore entries** (in the worktree — tail-byte-safe append
   exactly as ship P0 does, then commit if changed):
   ```bash
   cd <worktree>
   [ -f .gitignore ] && [ -n "$(tail -c1 .gitignore 2>/dev/null)" ] && printf '\n' >> .gitignore
   for e in '.claude-ship-state.json' '.claude-pr-fix-state*.json' '.claude-fleet-state.json' 'ship-run.log' 'ship-run.err.log' 'bootstrap.txt'; do
     grep -qxF "$e" .gitignore 2>/dev/null || echo "$e" >> .gitignore
   done
   git add .gitignore
   git diff --cached --quiet || git commit -m "chore: ignore ship/fleet state and log files"
   ```
   The glob `.claude-pr-fix-state*.json` covers fix-pr-reviews' numbered
   backups (`.claude-pr-fix-state.1016.bak.json`). Without the log entries,
   ship's P4/P5 commits sweep live log files into the PR.
3. **Seed** `<worktree>\.claude-ship-state.json` (Write tool) with ship's
   exact schema — `phase` per mode (`plan` → `"plan-review"`, `spec`/`bare`
   → `"spec-review"`):
   ```json
   {
     "topic": "<slug>",
     "spec": <spec_path or dictated bare path, or null in plan mode with no matching spec>,
     "plan": <plan_path or null>,
     "branch": "feat/<slug>",
     "default_branch": "<DEFAULT_BRANCH>",
     "pr": null,
     "phase": "<plan-review|spec-review>",
     "status": "in-progress",
     "focus_next": "<one sentence for the seeded phase, e.g. 'P3: reviewing-plans auto double-pass on the plan.'>",
     "phase_log": [ { "phase": "init", "result": "fleet-seeded worktree from origin/<DEFAULT_BRANCH>" } ],
     "blockers": [],
     "test_paths": [],
     "db_gate": null
   }
   ```
   Ship's First action treats this as an in-progress pipeline and resumes
   at `phase` — zero ship changes.
4. **Bootstrap + spawn** (next section).

**Setup-failure rollback:** any failure in steps 1–4 must not strand a
half-created instance — an orphaned `feat/<slug>` branch makes the issue
permanently un-fleetable via the branch-exists skip rule. Roll back
(`git worktree remove --force "<worktree>"`; `git branch -D "feat/<slug>"`)
and record the instance `skipped` with the failure as its reason; if
rollback itself fails, record `wedged` with the leftover paths so cleanup
and the final report cover it.

## Spawn

`Start-Process -ArgumentList` space-joins array elements without quoting,
so a prompt containing spaces (all of ours; bare mode's is multi-line)
arrives as shredded argv tokens — and `claude` may resolve to an npm `.cmd`
shim that won't launch under the redirect-implied `UseShellExecute=$false`.
The prompt therefore travels by file, and the process is a `pwsh` wrapper
piping it to stdin (`claude -p` with no prompt argument reads stdin):

```powershell
Set-Content -Path "$worktree\bootstrap.txt" -Value $bootstrap
$proc = Start-Process pwsh -PassThru -WindowStyle Hidden `
  -WorkingDirectory $worktree `
  -ArgumentList @('-NoProfile','-Command',
    'Get-Content -Raw .\bootstrap.txt | claude -p --dangerously-skip-permissions ' +
    '1> .\ship-run.log 2> .\ship-run.err.log')
# manifest: pid = $proc.Id
#           spawned_at = $proc.StartTime.ToUniversalTime().ToString('o')
```

Wrapper liveness equals run liveness **for natural exits only** (`pwsh`
exits when the pipeline exits). Killing the wrapper orphans the `claude`
child — deliberate termination is always `taskkill /PID <pid> /T /F`.

**Bootstrap prompts** (store the exact string in the manifest per instance;
respawns MUST reuse it):
- `plan` / `spec` mode: `Invoke the ship skill.`
- `bare` mode (idempotent — a respawn after a pre-spec crash must not
  double-author):
  `If docs/superpowers/specs/<today>-<slug>-design.md does not already exist and committed: read GitHub issue #N (gh issue view N), write a design doc to that exact path following the style of existing docs in docs/superpowers/specs/, and commit it. Then invoke the ship skill.`

Stagger spawns ~30s apart (soften the API burst). Issues beyond `--max`
enter the manifest as `queued` (no pid, no worktree yet — their worktree is
created when a slot frees, not upfront, so a dead fleet leaves no orphan
trees for never-started work).

## Fleet manifest

`.claude-fleet-state.json` at the **main repo root**, gitignored via
`.git/info/exclude`, written with the Write tool (full overwrite), updated
every tick. **All paths absolute** — `status`/`resume`/`cleanup` run in
later sessions where cwd may differ. Exception: `plan_path`/`spec_path`
are repo-relative (ship's own convention), stored per instance so a LATER
session (resume duty 2, post-compaction mid-setup recovery) can seed a
still-`queued` instance's ship state without re-running resolution — the
slug alone cannot recover them (date prefix stripped), and a plan-mode
seed with `plan:null` is a state ship cannot resume.

```json
{
  "fleet_id": "<YYYY-MM-DD>-issues-<first>-<last>",
  "default_branch": "main",
  "max_concurrent": 10,
  "monitor_pid": 5678,
  "monitor_heartbeat": "2026-07-13T14:05:00Z",
  "instances": [{
    "issue": 1032,
    "slug": "issue-1032-...",
    "mode": "plan",
    "worktree": "C:\\Users\\kmorg\\ship-fleet\\<primary-dirname>\\<slug>",
    "branch": "feat/<slug>",
    "pid": 1234,
    "spawned_at": "2026-07-13T14:00:00Z",
    "plan_path": "docs/superpowers/plans/2026-07-10-issue-1032-....md",
    "spec_path": null,
    "bootstrap": "Invoke the ship skill.",
    "log": "<worktree>\\ship-run.log",
    "restarts": 0,
    "last_snapshot_hash": null,
    "fleet_status": "queued|running|crashed|halted|awaiting-merge|done|wedged|skipped",
    "snapshot": { "phase": null, "status": null, "pr": null }
  }]
}
```

`fleet_status` semantics:
- `crashed` is written the moment the monitor detects dead+in-progress
  (BEFORE the respawn decision) and remains until the instance is next
  confirmed alive (→ `running`) or exhausts its rails (→ `wedged`/`halted`)
  — a crash is observable in the manifest, never silently absorbed.
- `halted` and `wedged` are **sticky for the monitor**: it refreshes their
  snapshots read-only (a human's interactive progress may transition them
  to `done`/`awaiting-merge`) but NEVER respawns them. Respawn authority
  for sticky instances belongs exclusively to `/ship-fleet resume`. This
  closes a race: a human clearing a gate interactively runs under an
  unknown PID; without stickiness the monitor would see dead+in-progress
  mid-session and spawn a headless twin into the same worktree.

**Single-writer rule:** `monitor_pid` + `monitor_heartbeat` make monitor
liveness decidable. The monitor stamps the heartbeat every tick; a
heartbeat older than 2× the poll interval (10 min) = dead monitor. Live
heartbeat → any other session is report-only (no manifest writes, no
spawns). Only a stale heartbeat authorizes takeover.

**Liveness check (PID reuse):** Windows recycles PIDs. Liveness = process
with that PID exists AND is `pwsh` AND its start time matches `spawned_at`
— compared in UTC on both sides, ±2s tolerance:

```powershell
# try/catch, not -ErrorAction SilentlyContinue: in the Claude Code
# PowerShell tool a suppressed non-terminating error still exits 1, and a
# dead PID is the NORMAL end-state of every instance — a healthy tick must
# not read as a failed command.
try { $p = Get-Process -Id $inst.pid -ErrorAction Stop } catch { $p = $null }
$alive = $false
if ($p -and $p.ProcessName -eq 'pwsh') {
  $delta = [math]::Abs(($p.StartTime.ToUniversalTime() - [datetime]::Parse($inst.spawned_at).ToUniversalTime()).TotalSeconds)
  if ($delta -le 2) { $alive = $true }
}
```

(`Get-Process` `StartTime` is local; `spawned_at` is stored UTC — skipping
the `.ToUniversalTime()` on either side classifies every live instance as
dead in any non-UTC timezone and double-spawns twins.)

## Monitor loop

Runs in the spawning session. Poll interval 5 minutes. **Tick mechanics:**
each tick is ONE PowerShell tool call with `timeout: 600000` that begins
with `Start-Sleep -Seconds 300`, then gathers facts for every instance
(liveness per the check above + raw state-file text); the session then
applies the classification rules below, rewrites the manifest (Write tool),
spawns/notifies as needed, prints the dashboard, and issues the next tick
call. Long fleets survive compaction via Compact instructions.

Each tick: stamp `monitor_heartbeat` (UTC now), then per instance —
skipping sticky `halted`/`wedged` except a read-only snapshot refresh:

1. Read `<worktree>\.claude-ship-state.json` + liveness check. **State
   read/parse failure is transient, never diagnostic** (ship's overwrite is
   not atomic; a poll can land mid-write): keep the previous `snapshot`,
   retry next tick, never classify an instance or abort the loop from a
   failed read.
2. **Alive** → `running`; refresh `snapshot` (phase/status/pr).
3. **Dead + terminal-clean state** (`status` ∈ blocked / awaiting-db-gates
   / done, or `phase == "awaiting-merge"`) → mark `halted` (blocked,
   awaiting-db-gates) / `awaiting-merge` / `done`; notify ONCE
   (PushNotification if available, else a loud dashboard line) carrying the
   blocker/checklist text verbatim. "Once" has a mechanism: notify only on
   the `fleet_status` TRANSITION — when this tick's manifest write changes
   the recorded value. The prior value is durable in the manifest, so the
   rule survives monitor takeover without a separate notified-flag.
4. **Dead + still `in-progress`** → mark `crashed`, then apply the rails
   IN ORDER before any respawn:
   - **db-gates rail:** `phase == "db-gates"` → ALWAYS a human gate (ship
     P6.5 sets the phase on entry, then asks its one apply-now/defer/abort
     question before writing any status; a headless run dies on that
     question). Mark `halted`, notify with the last ~20 lines of
     `ship-run.log`. Never respawn.
   - **No-progress rail:** hash the snapshot (`phase|status|pr` string). If
     it equals `last_snapshot_hash` from the previous respawn — the restart
     made zero progress; it is dying on the same question or hard error —
     mark `halted`, notify with the log tail, stop restarting.
   - **Genuine crash** (rate-limit kill, OOM, network): if `restarts < 2`,
     respawn — re-run the Spawn procedure (`claude -p
     --dangerously-skip-permissions` via the bootstrap file) with the
     instance's **stored `bootstrap`** (a bare-mode instance that died
     before authoring its spec must re-run the idempotent bare bootstrap —
     plain "Invoke the ship skill." at `spec-review` with a nonexistent
     spec just wedges); record new `pid`/`spawned_at`, set
     `last_snapshot_hash` to the current hash, increment `restarts`, →
     `running`. Else mark `wedged`, notify, leave state + logs for autopsy.
5. **Slot accounting:** running instances < `max_concurrent` and queue
   non-empty → run Per-instance setup + Spawn for the next `queued`
   instance (one per free slot, still ~30s staggered).
6. **Dashboard** (every tick):
   `issue | slug | phase | status | pr | restarts | fleet_status`.

**Fleet exit:** all instances terminal (`awaiting-merge`, `halted`, `done`,
`wedged`, `skipped`). Final report: PRs opened (URLs), gates awaiting the
human (blocker/checklist text), wedged autopsy pointers (log paths), skipped
issues with reasons + remedies.

The monitor never merges, never acks DB gates, never answers ship's
questions.

## Subcommands: status / resume / cleanup

**Clearing a gate (the human's path, documented for the operator):** `cd`
into the worktree, run `claude` interactively, invoke `/ship`. Ship's First
action surfaces the blocker/checklist in full context. The state file is
shared truth; the monitor (or next `status`) sees the outcome.

**`status`** — read-only, any session (manifest paths are absolute). Read
manifest + every worktree state file live; print the dashboard plus a
"needs you" list: blockers verbatim, DB-gate checklists, PRs awaiting
merge. Report monitor liveness (heartbeat age). Never writes.

**`resume`** — subject to the single-writer rule: live heartbeat →
report-only (print what it WOULD do and how to proceed); stale heartbeat →
act, with three duties:
1. For each instance with a dead PID and `in-progress` state, EXCLUDING
   the terminal-clean set (statuses blocked/awaiting-db-gates/done,
   `phase:"awaiting-merge"`) AND the db-gates rail (`phase:"db-gates"` +
   in-progress — an uncleared P6.5 gate; a headless respawn just re-asks
   its question to nobody): respawn headless (the Spawn procedure —
   `claude -p --dangerously-skip-permissions` via the bootstrap file) with
   the stored `bootstrap`, reset `restarts` to 0, clear
   `last_snapshot_hash` (human intervention earns fresh retries and re-arms
   the no-progress rail).
2. Spawn instances still `queued` — a dead monitor orphans the queue;
   queued instances have no PID or state file, so no other path starts
   them. Seed from the manifest's stored `plan_path`/`spec_path` (never
   guess from the slug). **Only into free slots:** liveness-confirmed
   running instances < `max_concurrent`; the restarted monitor's slot
   accounting drains the rest (headless instances survive a dead monitor
   session, so the cap must count them).
3. Restart the monitor loop, taking over `monitor_pid` + heartbeat.

**`cleanup`** —
- `done` instances (or instances whose PR reports MERGED via
  `gh pr view <pr> --json state`): `git worktree remove "<worktree>"`;
  if the remove fails because the tree is dirty, show the dirt and ask the
  human before `--force`; delete the branch if merged
  (`git branch -d "feat/<slug>"`, `git push origin --delete "feat/<slug>"`
  if it was pushed); drop the instance from the manifest.
- `wedged` / permanently-`halted` instances: offered INDIVIDUALLY with
  explicit confirmation — remove worktree, optionally delete the
  `feat/<slug>` branch (local + origin). Without this exit, a wedged
  instance's leftover branch makes its issue permanently un-fleetable via
  the branch-exists skip rule.
- When the last instance is dropped, archive the manifest to
  `<worktrees-root>\archive\<fleet_id>.json` and delete
  `.claude-fleet-state.json`.

## Edge cases

- Fleet invoked while a manifest with non-terminal instances exists →
  behaves as `status` + offers `resume`; never double-spawns (liveness
  check + heartbeat + single-writer rule are the guards).
- **A second fleet requires the first fully cleaned up, not merely
  terminal.** "Terminal" includes halted/awaiting-merge/wedged — live
  worktrees, branches, unanswered human gates that the single per-repo
  manifest still tracks; overwriting it would orphan them from every
  dashboard. Refuse until every instance is `done`/`skipped` AND worktrees
  removed (`cleanup`). Archive the prior manifest on new-fleet start.
- Dirty primary working tree is fine (worktrees branch from
  origin/<default>; the manifest write needs no clean tree) — EXCEPT Fleet
  setup step 3 must be able to fast-forward the local default branch; if it
  can't, the fleet stops before creating anything.
- Rate-limit storms kill instances → the crash path self-heals; the restart
  cap (2 per instance per unattended run) bounds token burn.
- An interactive gate session committing in a worktree is fine — the state
  file remains the single truth, and sticky halted/wedged keeps the monitor
  from spawning a headless twin while the human works.
- **Shared `.git` contention:** all worktrees share the primary repo's
  object store and refs; with ~10 instances fetching/committing/pushing,
  transient `index.lock`/`packed-refs` errors are expected occasionally.
  Ship converts such a failure to `status:"blocked"` — these are safely
  cleared by re-invoking `/ship` in the worktree; the "needs you" output
  says so when blocker text looks lock-shaped (mentions `index.lock` /
  `packed-refs` / `Another git process`).

## Coupling contract

Fleet depends on ship's documented state schema and First-action resume
semantics (ship SKILL.md "State file" section). One-way: ship never knows
fleet exists; if ship's schema changes, fleet breaks and gets updated; solo
`/ship` is never affected.

Load-bearing assumptions (a ship change breaking one of these breaks
fleet — check here first):
(a) ship's deliberate halts write `blocked`/`awaiting-db-gates`/`done` or
    reach `phase:"awaiting-merge"` before exiting;
(b) P6.5 asks its human decision BEFORE writing any status (hence the
    db-gates rail);
(c) First action resumes an `in-progress` state at `phase` after a
    branch-name check a fleet worktree satisfies by construction.

<!-- Task 6 -->
## First-run validation
