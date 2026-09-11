---
name: ollama-workers
description: Turn Ollama cloud models (glm-5.3-flash, glm-5.3, kimi-k3) on or off as the default implementers for superpowers plan execution, turn enforcement of that routing on or off, and report which model is active. Use when the user says /ollama-workers, asks to enable or disable ollama or GLM or Kimi workers, asks which worker model is active, wants to switch the worker model, asks to enforce or stop enforcing worker routing, asks whether plan tasks are running on an open model, asks whether the worker can dispatch from the current directory, asks why no task went to the worker, an implementer dispatch was denied by the ollama-workers routing gate, or a worker dispatch was refused because the session is isolated in a worktree.
---

# Ollama workers

Makes an Ollama cloud model the default implementer for plan execution, running
in a separate headless Claude Code process. The orchestrator stays on Anthropic,
and so does every reviewer: Anthropic models plan, route and check; the worker
writes the code.

State: `~/.claude/ollama-workers.json` - `{ "enabled", "model", "maxTurns",
"enforceRouting" }`. The wrapper enforces `enabled` itself and exits 1 without
launching anything unless it is `true`, so a dispatch on stale context fails
loudly instead of running. A missing or unreadable state file counts as off.

## Commands

**`status`** (also the bare invocation) - read the state file, print enabled +
model + maxTurns + enforceRouting, then probe this directory, then `ollama list`
so the user sees which cloud tags exist.

```powershell
pwsh -NoProfile -File "$HOME/.claude/scripts/ollama-worker.ps1" -Probe
```

Run it through the PowerShell tool. From Bash, a worktree-isolated session
refuses that line before the wrapper starts (see **Transport** under Dispatch
contract), and a refusal is not a probe answer - it says nothing about this
directory.

The probe runs the wrapper's own preflight against the current directory - the
model tag's syntax, the directory, the settings overlay, the worktree guard,
the ollama binary - and prints one JSON line: `dispatchable`, `reason`,
`remedy`, `enabled`, `model`, `model_syntax_ok`. Exit 0 means a dispatch from
here would get past the preflight, 1 means it would not.

`reason` names the *first* blocker, in the order a dispatch hits them, so fixing
it is what unblocks the next attempt. It is not a list: a directory with two
faults reports the first, and re-probing after the fix reveals the next.

Report it as **DISPATCHABLE** or **NOT DISPATCHABLE: <reason>**, and on a no,
the `remedy` line too. Never report enabled without it. `enabled`, `model`,
`maxTurns` and `ollama list` can all look correct in a directory where no
dispatch can ever succeed - a primary checkout is the ordinary case - and that
combination is exactly what turned this feature into a silent no-op for a whole
plan execution: nothing warned, and a skipped dispatch writes no log line.

`model_syntax_ok` is a syntax check, not proof the tag exists. Cloud tags pull
on first use, so a valid tag legitimately does not appear in `ollama list`;
say so rather than implying the model has been verified.

**`on [model]`** - set `enabled: true`. With no model, keep the stored one.
With a model, validate it first:

```powershell
ollama list
```

Accept the tag if `ollama list` shows it, or if it ends in `:cloud` (cloud tags
pull on first use and need not appear yet). Reject anything else and say why -
a typo becomes a 20-second failure per task otherwise. The wrapper re-checks the
tag against `^[A-Za-z0-9][A-Za-z0-9._:/-]*$` at dispatch and refuses to launch
on a mismatch, so a tag written into the state file by hand fails there too.

**`off`** - set `enabled: false`. Leave `model` alone so the next `on` remembers it.

**`enforce on`** / **`enforce off`** - set `enforceRouting`. While it and
`enabled` are both true, the PreToolUse gate
`~/.claude/hooks/ollama-workers-route-gate.py` denies an implementer dispatch
to any agent other than `ollama-worker` unless its prompt has a line
`ROUTING-EXCEPTION: <reason>` (see **Which tasks go to the worker**). The gate
reads the state file on every call, so the change reaches every running session
at once - `enforce off` is the kill switch if the gate ever gets in the way.
Say that when turning it on while other sessions are mid-pipeline: their next
Anthropic implementer dispatch will be denied until it carries a tag.

Write the file with a whole-object rewrite, preserving the keys you are not
changing. `Add-Member -Force` because an older state file has no
`enforceRouting` key:

```powershell
$p = "$HOME/.claude/ollama-workers.json"
$s = Get-Content -Raw $p | ConvertFrom-Json
$s.enabled = $true          # or $false
$s.model = 'glm-5.3-flash:cloud'
$s | Add-Member -NotePropertyName enforceRouting -NotePropertyValue $true -Force   # enforce on|off
$s | ConvertTo-Json | Set-Content -LiteralPath $p
```

After enabling, run the same probe `status` runs and report both facts in one
line - `on` enables the switch globally, but enabling in a directory that cannot
dispatch is not a success:

> workers on (glm-5.3-flash:cloud); this directory is NOT DISPATCHABLE: primary
> checkout - dispatch from a linked worktree.

After any change, state the new setting in one line. The change takes effect for
the next dispatch in this session - no restart needed, because this skill's text
is now in context.

## Dispatch contract (read this before dispatching a plan task)

**Precondition: `-Cwd` must be a linked git worktree.** The worker runs with
`--dangerously-skip-permissions`, so the wrapper accepts only a worktree (git
reports a different `--git-dir` and `--git-common-dir`) and exits 1 on a primary
checkout or a plain directory. Create one before the first dispatch -
`superpowers:using-git-worktrees`, or `git worktree add <path> <branch>` - and
pass that path. This is a step to take, not a reason to skip the worker: a plan
executed from a primary checkout should move to a worktree, not quietly route
every task to Anthropic. `/ship` is the caller that rule was written for - it
runs in a primary checkout by construction, so its P4 cuts a detached worktree
at the branch tip, dispatches with that as `-Cwd`, and fast-forwards each
finished task back into the branch the conductor still holds. Check any
directory without dispatching with `ollama-worker.ps1 -Probe -Cwd <path>`.

When enabled, replace an Anthropic **implementer** dispatch with:

```
Agent(subagent_type: "ollama-worker", model: "haiku",
      prompt: "-BriefFile <path> -Cwd <worktree> -Label <task-id>")
```

The forwarder is trivial, so it runs on haiku; the actual work runs on the
Ollama model in a child process. Write the brief to a file first - the same
brief you would have put in an Anthropic implementer prompt, including the
skills it must follow. `superpowers:subagent-driven-development`'s
`implementer-prompt.md` is unchanged; only the dispatch mechanism differs.

**Accepting a verdict.** Every verdict carries a `run_id`, and the wrapper
writes a run row with that id to `~/.claude/ollama-workers.log.jsonl` before it
prints the verdict. Before treating `ok: true` as done, confirm the row exists:

```powershell
@(Get-Content "$HOME/.claude/ollama-workers.log.jsonl" | Select-String -SimpleMatch '"run_id":"<run_id>"' | Where-Object { $_.Line -match '"event":"run"' }).Count
```

`0` means the worker never ran, whatever the reply says - a forwarder once did
a task itself, committed, and was credited to the worker. Discard its report
and anything it changed, and dispatch again. A reply with no JSON at all is the
same case.

**Transport.** The forwarder runs the wrapper through the PowerShell tool, and
has no other shell. Bash is the wrong transport because a session isolated in a
worktree - `EnterWorktree`, or an agent launched with worktree isolation - vets
every Bash command and refuses any that starts `pwsh`: Claude Code cannot show
that text handed to a second shell will not run git. That check is built into
Claude Code, not a hook, so there is nothing to allowlist. The PowerShell tool
is not vetted that way, and the identical command line runs there. It matters
here more than anywhere, because the precondition above puts every dispatch
inside a worktree, and entering one is what isolates a session.

A result that quotes "is isolated in the worktree ... Refusing to run it", or
starts `transport refused:`, means the wrapper never ran. It is not a verdict
and it is not the unavailable case below: nothing was probed, nothing was
logged, and the worker may be one tool call away. Run the same command yourself
through the PowerShell tool, with `run_in_background: true`, and route on its
JSON exactly as you would on the forwarder's:

```powershell
pwsh -NoProfile -File "$HOME/.claude/scripts/ollama-worker.ps1" -BriefFile <path> -Cwd <worktree> -Label <task-id>
```

That is the same dispatch, not a retry. Expect it from a forwarder installed
before this fix. Reinstalling fixes running sessions too: a session opened
before the reinstall picked up the new forwarder on its next dispatch.

When you run the wrapper yourself, you are the forwarder, so its rules are
yours. Do not end your turn while it runs - Claude Code terminates a background
command when the agent that started it gives its final response - so wait on
its output file in the foreground (the loop in `agents/ollama-worker.md`). If
its output shows `[killed]`, or you are told the command was stopped, the
verdict is `killed_by_harness` with the `run_id` from its
`ollama-worker: run_id=<id> started` line; route on it as below.

Roles that stay on Anthropic, always:

- task reviewers, scoped re-reviews, and the final code review - use **opus**
  for these, not sonnet. On Artificial Analysis, glm-5.3-flash scores 72 coding
  / 52 agentic against Sonnet 5's 72 / 45, so a sonnet gate is not above the
  worker it is grading.
- fix rounds 4-5 escalation implementers
- the plan-document reviewer

## Which tasks go to the worker

Every implementer task, by default. An Anthropic implementer is an exception,
and only these qualify:

- **the project's hazard list** - auth and permissions, DB migrations, money
  movement and settlement, webhook signature verification. Size does not matter
  for these.
- **tools the worker does not have** - MCP servers (Supabase and the like),
  production access, a logged-in CLI. Split the task where you can instead: the
  orchestrator runs the queries and pastes the results into the brief, and the
  worker writes the code. The worker has no MCP servers on purpose - it runs
  with `--dangerously-skip-permissions`.
- **design judgment** the plan left open.
- **fix rounds 4-5**, and a task the worker has already failed twice (killed or
  escalated).
- **no dispatchable worktree** (see When the worker is unavailable).

Name the exception in the dispatch prompt as a line
`ROUTING-EXCEPTION: <reason>`, and in the one-line routing note at the dispatch.
While `enforceRouting` is on, the gate denies an implementer dispatch without
one - and records every exception it lets through, with its reason.

The cost of this default is time, not quality. The endpoint has no prompt
caching: ~34K of system prompt is re-sent every turn and TTFT is ~20s. The first
16 completed tasks took a median of about 6 minutes and 14 turns, the longest
about 20 minutes. Multi-file tasks run longer and cross `maxTurns` more often
(see Escalation). That time is the price of keeping Anthropic tokens on
orchestration and review. Make the worker's job winnable: a brief that names the
exact files, functions and test command, with the code where the plan has it.

## When the worker is unavailable

Unavailable before a dispatch - workers off, or the probe says NOT DISPATCHABLE
and the directory cannot be moved - is not escalation. Nothing failed and no
evidence was earned. Route the task to the Anthropic tier
`superpowers:subagent-driven-development` Model Selection prescribes for it, as
if workers were off, with `ROUTING-EXCEPTION: <why the worker is unavailable>`
in the prompt: a fast, cheap model for mechanical work, a standard model for
integration and judgment. Do not promote a task to a larger model because the
worker was missing.

A transport refusal is not this case (see **Transport**). The wrapper never
ran, so availability is unknown rather than false, and taking this fallback on
it records a worker as missing that was one tool call away.

Say which happened, in one line, at the first affected dispatch. Silent
re-routing is the failure mode this skill has already produced once: an
orchestrator that read the worktree rule, correctly routed around it, and left
no error, no log line, and no mention that the feature was inert for the whole
plan.

## Escalation

Escalate on evidence, never predict. The wrapper exits 2 and sets `escalate`
when `is_error`, a nonzero child exit, `num_turns > maxTurns`, or the child's
stdout carries no usable result envelope; the forwarder reports
`killed_by_harness` when the machine killed the run. Route on `reason`:

| `reason` | What the caller does |
|---|---|
| `killed_by_harness` | Not evidence against the model: Claude Code stopped it, usually for low memory. Discard what it left in the worktree, then dispatch the same brief to the worker once more, **fresh** - never `-Resume` a killed session, whose memory describes edits you just discarded. A second kill goes to Anthropic with `ROUTING-EXCEPTION: worker killed twice`. |
| `turns_<n>_over_<max>` | Anthropic. See `maxTurns` under Calibration. |
| `is_error`, `exit_<n>`, `no_result_json_exit_<n>`, `invalid_result_json_exit_<n>` | Anthropic. |

Review rejecting the same task twice also sends it to Anthropic.

The ladder has two rungs: **ollama model -> Anthropic**. Never re-dispatch a
failed task to a larger Ollama model - it re-sends full context to a slower
endpoint with the same tool-format failure modes.

## Fix rounds

`superpowers:subagent-driven-development` resumes the original implementer for
fix rounds 1-3. For a task the worker implemented, resuming is a **new forwarder
dispatch** with `-Resume <session_id>` taken from the task's last verdict, the
same `-Cwd`, and a brief holding only the open findings, the tests that cover
them, and the report contract:

```
Agent(subagent_type: "ollama-worker", model: "haiku",
      prompt: "-BriefFile <fix-brief> -Cwd <worktree> -Resume <session_id> -Label <task-id>-fix<R>")
```

Never `SendMessage` the forwarder: it is a haiku shell with no memory of the
task, and it would re-run the wrapper without `-Resume`. The resumed worker
keeps its own context, so a fix round is usually a few turns. If the resumed run
comes back `is_error` (the session could not be resumed), dispatch fresh with
the full brief plus the findings. Rounds 4-5 go to Anthropic
(`ROUTING-EXCEPTION: fix round 4 escalation`).

## Calibration

Every dispatch appends two lines to `~/.claude/ollama-workers.log.jsonl`,
sharing a `run_id`: `event: "start"` before the worker launches, and
`event: "run"` after it exits, with model, num_turns, duration_ms, escalate and
reason. **A start with no run is a killed run** - before the start row existed,
7 of 29 real runs left no trace at all, because the run row is written only
once the child exits. After ~20 tasks, read it and adjust `maxTurns` and the
routing rubric from that instead of from published benchmarks.

`maxTurns` is a threshold, not a limit: this CLI has no `--max-turns`, so the
wrapper compares the result's `num_turns` after the fact, and a run over it
exits 2 with `turns_<n>_over_<max>` - which a caller that follows the table
above discards. Raising it keeps more of the worker's longer runs; lowering it
sends them to Anthropic sooner.

A probe that finds the directory not dispatchable while workers are on appends
`event: "probe"` with `dispatchable: false` and a reason. Read those as
availability, not outcomes: filter them out of turn and escalation statistics,
and read a run of them as "the worker was never usable in this repo" rather than
"no task was a good fit." Without them the log could not tell those two apart,
because a task that is never dispatched writes nothing at all. Rows written
before this field exists have no `event` key and are runs.

`event: "gate"` rows come from the routing gate: `decision: "deny"` for an
implementer dispatch it refused, `decision: "exception"` with the tag's `reason`
for one it let through to Anthropic. They are the record of why a task did not
go to the worker.

## Notes

- The worker runs under `CLAUDE_CONFIG_DIR=~/.claude-ollama-worker` with
  `plugins` junctioned to `~/.claude/plugins`. Sessions live at
  `<config-dir>/projects/<cwd>/`, so sharing the caller's config dir would put
  worker transcripts where the caller's next `claude --continue` would resume
  them - and a session produced by a non-Anthropic backend fails to resume
  against the Anthropic API.
- The worker runs with `--dangerously-skip-permissions`, matching how
  `ship-fleet` spawns headless instances. It has to edit files and run tests
  with nobody there to answer a prompt. That is why `-Cwd` is checked and not
  trusted - see the precondition in Dispatch contract. Note the guard also
  rejects a plain scratch directory, and that a scratch path under a
  version-controlled home directory resolves to *that* repo and is reported as
  a primary checkout.
- The routing gate recognises implementer dispatches by how the prompt opens
  ("You are implementing Task N", "Fix round 1 for Task N", ...) or, for a
  prompt that only points at a dispatch file, by a description like
  "Implement Task 3: ...". A dispatch that names neither is not seen: the gate
  is a nudge that records its decisions, not a proof.
- Tests: `python ollama-workers/tests/test_route_gate.py`,
  `python ollama-workers/tests/test_status_hook.py`, and
  `Invoke-Pester ollama-workers/tests`. They run against a throwaway
  `OLLAMA_WORKERS_HOME`, never the real state file or log.
