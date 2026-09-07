---
name: ollama-workers
description: Turn Ollama cloud models (glm-5.3-flash, glm-5.3, kimi-k3) on or off as implementers for superpowers plan execution, and report which model is active. Use when the user says /ollama-workers, asks to enable or disable ollama or GLM or Kimi workers, asks which worker model is active, wants to switch the worker model, or asks whether plan tasks are running on an open model.
---

# Ollama workers

Routes short-turn mechanical implementer tasks to an Ollama cloud model in a
separate headless Claude Code process. The orchestrator stays on Anthropic, and
so does every reviewer.

State: `~/.claude/ollama-workers.json` - `{ "enabled", "model", "maxTurns" }`.

## Commands

**`status`** (also the bare invocation) - read the state file, print enabled +
model + maxTurns, then `ollama list` so the user sees which cloud tags exist.

**`on [model]`** - set `enabled: true`. With no model, keep the stored one.
With a model, validate it first:

```powershell
ollama list
```

Accept the tag if `ollama list` shows it, or if it ends in `:cloud` (cloud tags
pull on first use and need not appear yet). Reject anything else and say why -
a typo becomes a 20-second failure per task otherwise.

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

After any change, state the new setting in one line. The change takes effect for
the next dispatch in this session - no restart needed, because this skill's text
is now in context.

## Dispatch contract (read this before dispatching a plan task)

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

## Escalation

Escalate on evidence, never predict. The wrapper exits 2 and sets `escalate`
when `is_error`, a nonzero child exit, or `num_turns > maxTurns`. The caller
escalates on that, or when review rejects the same task twice.

The ladder has two rungs: **ollama model -> Anthropic**. Never re-dispatch a
failed task to a larger Ollama model - it re-sends full context to a slower
endpoint with the same tool-format failure modes.

## Calibration

Every run appends one line to `~/.claude/ollama-workers.log.jsonl`: model,
num_turns, duration_ms, escalate, reason. After ~20 tasks, read it and adjust
`maxTurns` and the routing rubric from that instead of from published
benchmarks.

## Notes

- The worker runs under `CLAUDE_CONFIG_DIR=~/.claude-ollama-worker` with
  `plugins` junctioned to `~/.claude/plugins`. Sessions live at
  `<config-dir>/projects/<cwd>/`, so sharing the caller's config dir would put
  worker transcripts where the caller's next `claude --continue` would resume
  them - and a session produced by a non-Anthropic backend fails to resume
  against the Anthropic API.
- The worker runs with `--dangerously-skip-permissions`, matching how
  `ship-fleet` spawns headless instances. It has to edit files and run tests
  with nobody there to answer a prompt.
- This CLI has no `--max-turns`, so `maxTurns` is checked after the fact from
  the result JSON. It is an escalation signal, not a hard stop.
