---
name: ollama-workers
description: Turn Ollama cloud models (glm-5.3-flash, glm-5.3, kimi-k3) on or off as implementers for superpowers plan execution, and report which model is active. Use when the user says /ollama-workers, asks to enable or disable ollama or GLM or Kimi workers, asks which worker model is active, wants to switch the worker model, asks whether plan tasks are running on an open model, asks whether the worker can dispatch from the current directory, asks why no task went to the worker, or a worker dispatch was refused because the session is isolated in a worktree.
---

# Ollama workers

Routes short-turn mechanical implementer tasks to an Ollama cloud model in a
separate headless Claude Code process. The orchestrator stays on Anthropic, and
so does every reviewer.

State: `~/.claude/ollama-workers.json` - `{ "enabled", "model", "maxTurns",
"timeoutMinutes", "maxConcurrent" }`. The last three default to 100, 25 and 1
when absent. The wrapper enforces `enabled` itself and exits 1 without
launching anything unless it is `true`, so a dispatch on stale context fails
loudly instead of running. A missing or unreadable state file counts as off.

Workers are opt-in. Nothing - a skill, a hook, a pipeline rule - should force
work through a worker dispatch, and a conductor that could make an edit itself
should not be made to dispatch it instead.

Every dispatch is bounded: `maxTurns` is passed to the headless run as
`--max-turns`, the wrapper kills the worker's whole process tree after
`timeoutMinutes` of wall time, and it refuses a dispatch while `maxConcurrent`
workers are already running. Each of those ends in a verdict, not a hang.

`maxTurns` guards against a runaway loop; it is not a measure of fit. A hard
stop cuts a worker off mid-task and leaves partial edits, and the 14 runs an
older after-the-fact check escalated at 27 to 86 turns had all finished with a
commit in under 18 minutes. Keep it well above what real tasks take, and let
`timeoutMinutes` be the bound that matters.

Keep `timeoutMinutes` at or below 35: the forwarder stops after ten checks of
about 4 minutes, and a longer limit lets it give up while the worker is still
editing the worktree the next implementer will be sent into.

## Commands

**`status`** (also the bare invocation) - read the state file, print enabled +
model + maxTurns + timeoutMinutes + maxConcurrent, then probe this directory,
then `ollama list` so the user sees which cloud tags exist.

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
`remedy`, `enabled`, `model`, `model_syntax_ok`, `max_turns`,
`timeout_minutes`, `max_concurrent`. Exit 0 means a dispatch from here would
get past the preflight, 1 means it would not. The probe does not count running
workers: a full concurrency cap is a moment, not a fact about the directory.

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

Write the file with a whole-object rewrite, preserving the keys you are not
changing:

```powershell
$p = "$HOME/.claude/ollama-workers.json"
$s = Get-Content -Raw $p | ConvertFrom-Json
$s.enabled = $true          # or $false
$s.model = 'glm-5.3-flash:cloud'
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
every task to Anthropic. Check it without dispatching with
`ollama-worker.ps1 -Probe -Cwd <path>`.

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

**Transport.** The forwarder runs the wrapper through the PowerShell tool only.
Bash is the wrong tool because a session isolated in a worktree - `EnterWorktree`, or an agent
launched with worktree isolation - vets every Bash command and refuses any
that starts `pwsh`: Claude Code cannot show that text handed to a second shell
will not run git. That check is built into Claude Code, not a hook, so there is
nothing to allowlist. The PowerShell tool is not vetted that way, and the
identical command line runs there. It matters here more than anywhere, because
the precondition above puts every dispatch inside a worktree, and entering one
is what isolates a session.

A result that quotes "is isolated in the worktree ... Refusing to run it", or
starts `transport refused:`, means the wrapper never ran. It is not a verdict
and it is not the unavailable case below: nothing was probed, nothing was
logged, and the worker may be one tool call away. Run the same command yourself
through the PowerShell tool, with `run_in_background: true`, then wait on it the
way the forwarder does - foreground checks of its output file, each returning
within about 4 minutes, until one says `STATE: finished` or `STATE: no verdict`
- and route on the JSON exactly as you would on the forwarder's:

```powershell
pwsh -NoProfile -File "$HOME/.claude/scripts/ollama-worker.ps1" -BriefFile <path> -Cwd <worktree> -Label <task-id>
pwsh -NoProfile -File "$HOME/.claude/scripts/ollama-worker.ps1" -Await '<output file>'
```

That is the same dispatch, not a retry. Expect it from a Bash-capable forwarder
installed before the PowerShell fix, and in any session opened before a
reinstall: agent definitions load at session start, so that session keeps the
old forwarder until it restarts.

Roles that stay on Anthropic, always:

- task reviewers, scoped re-reviews, and the final code review - use **opus**
  for these, not sonnet. On Artificial Analysis, glm-5.3-flash scores 72 coding
  / 52 agentic against Sonnet 5's 72 / 45, so a sonnet gate is not above the
  worker it is grading.
- fix rounds 4-5 escalation implementers
- the plan-document reviewer

## Which tasks go to the worker

The endpoint has no prompt caching: ~34K of system prompt is re-sent every turn
and TTFT is ~20s. Turn count, not the benchmark index, decides whether a task
is a good fit.

Send to the worker:

- 1-2 files, brief names the exact functions and tests, no cross-module contract
- self-contained boilerplate, mechanical refactors, single-file test writing

Keep on Anthropic in-process (cached, native tools):

- multi-file or integration work, pattern-matching against existing code -
  these are the long-turn tasks, and long turns are exactly what an uncached
  endpoint punishes
- design judgment, or anything on the project's hazard list - money movement
  and settlement, DB migrations, webhook signature verification, auth and
  permissions. Size does not matter for these.

## When the worker is unavailable

Unavailable before a dispatch - workers off, or the probe says NOT DISPATCHABLE
and the directory cannot be moved - is not escalation. Nothing failed and no
evidence was earned. Route the task to the Anthropic tier
`superpowers:subagent-driven-development` Model Selection prescribes for it, as
if workers were off: a fast, cheap model for mechanical work, a standard model
for integration and judgment. Do not promote a worker-shaped task to a larger
model because the worker was missing.

A verdict with `reason: "concurrency_cap"` is this case too, even though it
says `escalate: true`. `maxConcurrent` workers were already running, so nothing
ran and nothing was learned about the task: route it as unavailable, not as an
escalation, and do not wait and re-dispatch - that is the queue the cap
deliberately does not have.

A transport refusal is not this case (see **Transport**). The wrapper never
ran, so availability is unknown rather than false, and taking this fallback on
it records a worker as missing that was one tool call away.

Say which happened, in one line, at the first affected dispatch. Silent
re-routing is the failure mode this skill has already produced once: an
orchestrator that read the worktree rule, correctly routed around it, and left
no error, no log line, and no mention that the feature was inert for the whole
plan.

## Escalation

Escalate on evidence, never predict. The wrapper exits 2 and sets `escalate`,
and `reason` says why:

| reason | what happened |
|---|---|
| `max_turns_<n>` | the run hit `--max-turns` and was stopped |
| `turns_<n>_over_<m>` | the run reported more turns than the cap anyway |
| `timeout_<n>m` | past `timeoutMinutes`; the worker's process tree was killed |
| `is_error`, `exit_<n>` | the child reported an error or exited nonzero |
| `no_result_json_exit_<n>`, `invalid_result_json_exit_<n>` | no usable result envelope |
| `wrapper_error` | the wrapper itself failed after launching; `result` has the message |
| `wrapper_died`, `wrapper_exit_<n>` | the wrapper was killed from outside; recorded by `-Await` |
| `wrapper_overdue` | the wrapper outlived its own limit and `-Await` killed it |
| `forwarder_check_cap` | the forwarder ran out of checks |
| `concurrency_cap` | nothing ran - see **When the worker is unavailable** |

The caller escalates on those, or when review rejects the same task twice. Every
one except `concurrency_cap` means the worktree may hold partial work from the
worker; the Anthropic implementer starts from what is there.

The ladder has two rungs: **ollama model -> Anthropic**. Never re-dispatch a
failed task to a larger Ollama model - it re-sends full context to a slower
endpoint with the same tool-format failure modes.

## Calibration

Every launched run appends two lines to `~/.claude/ollama-workers.log.jsonl`
sharing a `run_id`: `event: "start"` before the launch, and `event: "run"` when
it ends - model, num_turns, duration_ms, wall_ms, escalate, reason, and
`leftover_processes`, the number of processes the worker left running that the
wrapper then killed. The run row is written on every path out of the wrapper,
including a timeout and a wrapper error. A wrapper killed from outside cannot
write one; `-Await` writes it instead, with `recorded_by: "await"`, and a start
row with no run row at all is a run nobody was waiting for. After ~20 tasks,
read the run rows and adjust `maxTurns`, `timeoutMinutes` and the routing rubric
from them instead of from published benchmarks.

A dispatch refused by the concurrency cap appends `event: "refused"`. Like
probe rows, those are availability, not outcomes.

A probe that finds the directory not dispatchable while workers are on appends
`event: "probe"` with `dispatchable: false` and a reason. Read those as
availability, not outcomes: filter them out of turn and escalation statistics,
and read a run of them as "the worker was never usable in this repo" rather than
"no task was a good fit." Without them the log could not tell those two apart,
because a task that is never dispatched writes nothing at all. Rows written
before this field exists have no `event` key and are runs.

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
- `--max-turns` works in `-p` mode even though `claude --help` does not list
  it, and `ollama launch claude ... --` passes it through. A capped run reports
  `num_turns` one above the cap. The after-the-fact `num_turns > maxTurns`
  check stays as a second layer.
- The wrapper waits on the `ollama` launcher alone, not on its descendants, and
  puts the worker's process tree in a job object. When the launcher exits, or
  the time limit passes, or the wrapper itself dies, everything left in the job
  is killed - a dev server or watch-mode test the worker started included.
  `Start-Process -Wait` waits for every descendant, which is how a worker that
  finished in 16 minutes held its wrapper for 8 hours.
