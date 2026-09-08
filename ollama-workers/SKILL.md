---
name: ollama-workers
description: Turn Ollama cloud models (glm-5.3-flash, glm-5.3, kimi-k3) on or off as implementers for superpowers plan execution, and report which model is active. Use when the user says /ollama-workers, asks to enable or disable ollama or GLM or Kimi workers, asks which worker model is active, wants to switch the worker model, asks whether plan tasks are running on an open model, asks whether the worker can dispatch from the current directory, or asks why no task went to the worker.
---

# Ollama workers

Routes short-turn mechanical implementer tasks to an Ollama cloud model in a
separate headless Claude Code process. The orchestrator stays on Anthropic, and
so does every reviewer.

State: `~/.claude/ollama-workers.json` - `{ "enabled", "model", "maxTurns" }`.
The wrapper enforces `enabled` itself and exits 1 without launching anything
unless it is `true`, so a dispatch on stale context fails loudly instead of
running. A missing or unreadable state file counts as off.

## Commands

**`status`** (also the bare invocation) - read the state file, print enabled +
model + maxTurns, then probe this directory, then `ollama list` so the user sees
which cloud tags exist.

```powershell
pwsh -NoProfile -File "$HOME/.claude/scripts/ollama-worker.ps1" -Probe
```

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

Say which happened, in one line, at the first affected dispatch. Silent
re-routing is the failure mode this skill has already produced once: an
orchestrator that read the worktree rule, correctly routed around it, and left
no error, no log line, and no mention that the feature was inert for the whole
plan.

## Escalation

Escalate on evidence, never predict. The wrapper exits 2 and sets `escalate`
when `is_error`, a nonzero child exit, `num_turns > maxTurns`, or the child's
stdout carries no usable result envelope. The caller escalates on that, or when
review rejects the same task twice.

The ladder has two rungs: **ollama model -> Anthropic**. Never re-dispatch a
failed task to a larger Ollama model - it re-sends full context to a slower
endpoint with the same tool-format failure modes.

## Calibration

Every run appends one line to `~/.claude/ollama-workers.log.jsonl` with
`event: "run"`: model, num_turns, duration_ms, escalate, reason. After ~20
tasks, read it and adjust `maxTurns` and the routing rubric from that instead of
from published benchmarks.

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
- This CLI has no `--max-turns`, so `maxTurns` is checked after the fact from
  the result JSON. It is an escalation signal, not a hard stop.
