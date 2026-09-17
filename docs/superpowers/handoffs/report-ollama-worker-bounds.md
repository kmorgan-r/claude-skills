# Report: bound the ollama worker

**Written:** 2026-09-17. **Branch:** `fix/ollama-worker-bounds` off `origin/main` (`f18f853`),
worktree `~/cs-wt/ow-bounds`. Not pushed, no PR. Answers `docs/superpowers/handoffs/kickoff-ollama-worker-bounds.md`, which
is untracked in `~/claude-skills-main` and not part of this branch.

## Headline: two findings contradict the kickoff

1. **hero-task-11 did not hang in the model.** The worker finished its task. The wrapper hung
   because `Start-Process -Wait` waits for every descendant process, and the worker left one
   running. The fix for that is to wait on the launcher alone, not only to add a timeout.
2. **`taskkill /T` is not enough to kill a worker.** Once the `ollama` launcher has exited,
   `taskkill /T` on its PID finds nothing, while `claude.exe` and its shells keep running. The
   wrapper now puts the worker tree in a Windows job object.

## Research

### R1. Hard turn cap: works, and `num_turns` lands above the cap

Ran, with `CLAUDE_CONFIG_DIR=~/.claude-ollama-worker`, in a scratch directory:

```
'Use your shell tool to run the command: echo r1-probe . Then reply with exactly what it printed.' |
  ollama launch claude --model glm-5.3-flash:cloud -- --settings ~/.claude/ollama-settings.json -p
  --output-format json --max-turns 1 --allowedTools 'Bash(echo:*)' 'PowerShell(echo:*)'
```

Result after 4.5 s, exit 1, stderr `Error: exit status 1`:
`type: result`, `subtype: error_max_turns`, `is_error: true`, **`num_turns: 2`**,
`stop_reason: tool_use`, `errors: ["Reached maximum number of turns (1)"]`.

- Claude Code 2.1.274 accepts `--max-turns` in `-p` mode although `claude --help` does not list
  it, and ollama 0.34.0 passes it through after `--`.
- The kickoff said a capped run "may report `num_turns` equal to the cap". It reports one above.
  The old post-run check would fire, but `is_error` came first in the old code, so the reason
  would have read `is_error`. The wrapper now checks `subtype` first.

### R2. Why hero-task-11 hung: the wrapper waited on a leftover process

- Worker transcript:
  `~/.claude-ollama-worker/projects/C--Users-kmorg-cp-hero-carousel/198be09e-74f9-42c9-9054-60fddaca79fa.jsonl`,
  created 17:31Z, last write 17:47Z. The last assistant message (17:47:19Z) has
  `stop_reason: end_turn` and reports `DONE_WITH_CONCERNS`, commit `aac8c51d2`.
- The wrapper's stdout temp file was never cleaned up because the wrapper was killed:
  `%TEMP%\tmpxhhbyq.tmp`, last written 17:47:19.77Z, holds a complete result envelope
  (`stop_reason: end_turn`, `session_id: 198be09e-...`). So `claude.exe` printed its result.
- Forwarder transcript: the 35th poll at 01:42Z got `[exited with code 255]`, followed by
  `[Request interrupted by user]`. A user interrupt ended it, not the worker.
- `Start-Process -Wait` semantics, measured on pwsh 7.6.6: a parent that exited at once, leaving
  a detached 25-second grandchild, made `-Wait` return after 26.1 s.
- What the worker left running (**inference**, not recorded): at 17:40 it ran a throwaway probe
  that did `spawn('npx', ['serve', '-s', 'dist', '-l', '4199'], { stdio: 'ignore', shell: true })`,
  and its first `npm run build:seo` (prerender with a spawned server) failed. Either could have
  left a server alive.

### R3. Background commands survive the subagent, but the caller gets an interim reply

A Haiku subagent launched `Set-Content started.txt; Start-Sleep 90; Set-Content done.txt` with
`run_in_background: true` and ended its turn at once.

- The agent's first result arrived after 9.6 s: `LAUNCHED bmsgg01t3`, with the note "This agent
  stopped with background work of its own still running ... the result below may be interim."
- `done.txt` was written at 14:02:39Z, 90 s after `started.txt` (14:01:09Z). The command survived.
- The harness then re-woke the agent and delivered a second result.

So #35's forwarder was wrong that a background command is "terminated when you give your final
response", and the reverted forwarder's launch works. But a caller taking the Agent result as the
verdict would get `LAUNCHED`, not JSON. The forwarder therefore still waits in the foreground, in
bounded checks (change 3). Evidence from a forwarder transcript shows its prompt cache is
`ephemeral_5m`, so checks now return within 240 s.

### R4. Killing the process tree: the launcher alone leaves claude.exe alive

One real launch (`--max-turns 3`, `--allowedTools 'Bash(sleep:*)'`), assigned to a job object
right after `Start-Process`:

- Tree under the launcher: `ollama.exe` > `claude.exe` > `bash.exe` x3 + `conhost.exe`. Job
  active count 7.
- `Stop-Process` on `ollama.exe` only: launcher gone, **`claude.exe` alive**, its bash children
  alive, job active count 5.
- `taskkill /F /T /PID <launcher>` after that: `ERROR: The process "45028" not found.`
  `claude.exe` still alive.
- `TerminateJobObject`: `claude.exe` gone, job active count 0.

With fake trees (no model): a detached grandchild whose parent had exited survived without a job
(control marker written), and was killed both by `TerminateJobObject` and by the owning process
simply exiting (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).

### R5. Counting live workers: named mutex slots

`Local\ollama-worker-<hash of claude home>-slot-<i>` for `i < maxConcurrent`, taken with
`WaitOne(0)` and held until the wrapper exits.

- Measured: slot busy while a holder process lives; free immediately after `Stop-Process` on the
  holder. The kernel destroyed the mutex with the holder's last handle, so no stale lock file and
  no PID reuse.
- A named semaphore does not give its count back when a holder dies.
- A live wrapper is a live worker: the job object kills the worker tree when the wrapper dies,
  however it dies, so the slot count cannot miss an orphaned worker.
- The home-dir hash keeps test runs (`OLLAMA_WORKERS_HOME`) from competing with real workers.

### R6. Timeout default: 25 minutes

From `~/.claude/ollama-workers.log.jsonl` (462 rows: 334 probe, 53 start, 75 runs):

- 58 successful runs, `duration_ms` in minutes: p50 2.2, p75 4.0, p90 7.4, p95 8.2, max 19.5.
  Three runs over 13 minutes (13.4, 16.7, 19.5).
- 52 runs have both start and run rows: wall time exceeded `duration_ms` by at most 0.6 minutes
  (usually 0.0-0.1). Longest wall time 13.7 minutes in that subset.
- The only unpaired start row is hero-task-11.
- The 14 turn-cap overruns ran 1.2-17.3 minutes; `--max-turns` now stops those earlier.

25 minutes is above every success and leaves about 28% headroom over the longest.

### R7. Drift and leftovers: confirmed

- `~/.claude/ollama-workers.json`: `enabled: false`, `glm-5.3-flash:cloud`, `maxTurns: 25`.
- Installed `agents/ollama-worker.md`, `scripts/ollama-worker.ps1` and
  `hooks/ollama-workers-status.py` are byte-for-byte the `bc1bfe88` (#35) versions, and differ
  from `main`. The installed hook still tells sessions to dispatch "by default" and mentions
  `ROUTING-EXCEPTION`.
- `~/.claude/hooks/ollama-workers-route-gate.py` does not exist; `settings.json` references only
  `ollama-workers-status.py`.
- `~/.claude/skills/ollama-workers` is a junction to `~/claude-skills-main/ollama-workers`.

### R8. Conductor rule: gone

`Select-String` over `ship/` on `main` for `writes no source|conductor writes|ollama|ROUTING-EXCEPTION|worker`
found nothing. No change to `ship/`.

## Changes

| File | Change | Why |
|---|---|---|
| `ollama-workers/scripts/ollama-worker.ps1` | `OLLAMA_WORKERS_HOME` seam, retrying `Write-LogRow`, start row + `run_id` (from `bc1bfe88`) | tests must not touch the real state or log; a killed run shows as an unpaired start row |
| | `--max-turns $MaxTurns`; `subtype: error_max_turns` maps to `max_turns_<n>` before `is_error`; post-run turn check kept | R1 |
| | `-PassThru` + `WaitForExit(timeout)` on the launcher; worker tree in a job object with `KILL_ON_JOB_CLOSE`; job terminated after every run; `taskkill /T` too on timeout | R2, R4 |
| | `timeoutMinutes` (default 25, `[double]`), reason `timeout_<n>m` | R6 |
| | `maxConcurrent` (default 1), mutex slots, refuse with `concurrency_cap`, log `event: "refused"` | R5; refused rows stay out of run statistics |
| | run row on every exit path after launch, incl. timeout and `wrapper_error`; adds `wall_ms`, `leftover_processes` | killed runs stop disappearing; leftovers become visible |
| | `-Await <output file>` mode: checks up to 240 s; `finished` on a verdict line; synthesizes `wrapper_died` / `wrapper_exit_<n>` (and a run row) when the wrapper's PID is gone; kills the wrapper and returns `wrapper_overdue` past timeout + 180 s; `no verdict` when the wrapper refused before launching | bounded, tested wait for the forwarder (R3) |
| | `-Probe` and `-DryRun` report the two new settings | status and tests |
| `ollama-workers/agents/ollama-worker.md` | PowerShell only; background launch; at most 10 `-Await` checks; `forwarder_check_cap`; never does the task | change 3 |
| `ollama-workers/tests/Wrapper.Tests.ps1` | new, 21 Pester tests with a fake `ollama` | change 6 |
| `ollama-workers/SKILL.md` | state keys; opt-in line; bounds; reason table; `concurrency_cap` routed as unavailable; `-Await` in the transport fallback; calibration rows; replaced "no `--max-turns`" note; keep `timeoutMinutes` <= 35 | docs were false after the change |
| `ollama-workers/ollama-workers.example.json` | adds `timeoutMinutes: 25`, `maxConcurrent: 1` | fresh installs |
| `README.md` | ollama-workers section: bounds, PowerShell-only forwarder, log rows | docs were false |
| `advisor-bridge/scripts/advisor-bridge.ps1` | two comments cite `ollama-worker.ps1` line numbers; bumped to the new lines | they pointed at the wrong code |

Not changed: `install.ps1`, the status hook, `ship/`.

## Tests

```
Invoke-Pester (Pester 5.9.1) ollama-workers/tests/Wrapper.Tests.ps1
passed=21 failed=0 in 80s     (first green run)
passed=21 failed=0 in 74s     (rerun after the follow-up commit's edits)
```

Before the implementation the same file ran 1 passed, 20 failed. Most of those failed on the
missing seam, so each key test was also checked against a deliberately broken wrapper:

| Mutation | Test | Result |
|---|---|---|
| wait until the job is empty (`Start-Process -Wait` behaviour) | does not wait for a process the worker left running | failed: took 93.0 s, limit 45 |
| `LimitFlags = 0` (no kill on close) | takes the worker tree down when the wrapper itself is killed | failed |
| never assign to the job | does not wait for a process the worker left running | failed: grandchild survived |
| always take a slot | refuses a dispatch while maxConcurrent workers are running | failed: exit 0, expected 2 |

The wrapper was restored after the mutation run (checked by content comparison).

## Residual risks

- **No end-to-end dispatch through the new wrapper against a real model.** The job object was
  verified against the real launcher in R4, and the wrapper against a fake `ollama`. Free memory
  was 1.7-2.8 GB during this session, so I did not launch more headless workers.
- **Assign race.** The job is assigned right after `Start-Process` returns. In R4 every child
  landed in the job, but that is timing. A child spawned before assignment would escape; on
  timeout `taskkill /T` still reaches it while the launcher lives, after a normal exit it would
  be missed.
- **`-Await` depends on the start line format and the harness's `[exited with code N]` marker.**
  The marker only refines the reason; a dead wrapper is detected by PID and start time.
- **`timeoutMinutes` above ~37** lets the forwarder's 10 checks run out first. Documented, not
  enforced.

## What the human still has to do

1. **Review** `ceee6fc` and the follow-up commit on `fix/ollama-worker-bounds`, then push and open
   the PR if it looks right.
2. **Deploy after merge, not from the branch.** `install.ps1` copies `SKILL.md` into
   `~/.claude/skills/ollama-workers/`, which is a junction into `~/claude-skills-main`, so running
   it from this worktree would write the branch's `SKILL.md` into your main checkout next to your
   uncommitted `find-cold-leads` work. After merging and pulling `main` in
   `~/claude-skills-main`, run `./ollama-workers/install.ps1 -DryRun`, then without `-DryRun`.
   Installed vs branch today:
   - `agents/ollama-worker.md`: 35 insertions, 39 deletions
   - `scripts/ollama-worker.ps1`: 440 insertions, 64 deletions
   - `hooks/ollama-workers-status.py`: 10 insertions, 27 deletions (removes #35's "by default"
     and `ROUTING-EXCEPTION` text)
   - `skills/ollama-workers/SKILL.md`: 82 insertions, 27 deletions
3. **Start a new session after installing.** Agent definitions load at session start.
4. `ollama-workers.json` is seeded only when absent, so yours keeps three keys. The defaults
   (25 minutes, 1 worker) apply; add the keys only to change them.
5. **Safe to turn back on?** Yes, with conditions: opt-in only, `maxConcurrent: 1`, installed
   copies re-synced, a fresh session. Run one small real dispatch first and check its `start` and
   `run` rows. Then watch the first 5-10 run rows for `leftover_processes > 0`, `wall_ms` far
   above `duration_ms`, and `timeout_*` or `max_turns_*` reasons.
