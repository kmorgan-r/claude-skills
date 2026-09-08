# Advisor bridge: reaching Fable 5.1 from an Ollama-backed session

**Date:** 2026-09-08
**Status:** approved, ready for implementation planning

## Problem

`ollama launch claude` runs Claude Code against a local Ollama endpoint, so the
session's model is GLM (or Kimi). Claude Code's built-in `advisor` tool — a
stronger reviewer that receives the full transcript — is unavailable there, and
would be useless even if it were available. Three measured facts:

1. **The tool is disabled by design.** Launching with `--model
   glm-5.3-flash:cloud` prints:

   > Warning: Advisor disabled — base model 'glm-5.3-flash:cloud' has no advisor
   > rank in the model catalog. Switch to a public model alias (opus, sonnet,
   > fable) or set `CLAUDE_CODE_ENABLE_EXPERIMENTAL_ADVISOR_TOOL=1`.

2. **The escape hatch does not work.** With
   `CLAUDE_CODE_ENABLE_EXPERIMENTAL_ADVISOR_TOOL=1` the warning disappears but
   calling the tool returns `Error: No such tool available: advisor`.

3. **Registering it would not help.** An Ollama-launched session exports
   `ANTHROPIC_BASE_URL=http://127.0.0.1:11434` and points all three
   `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` vars plus
   `CLAUDE_CODE_SUBAGENT_MODEL` at the Ollama tag. One process serves one
   endpoint. An in-process advisor would be GLM advising GLM.

Control checks confirm the two halves work independently: `claude -p --model
claude-fable-5-1` succeeds against Anthropic, and the native `advisor` tool does
register in headless `-p` mode on an Anthropic model. Headless is not the
blocker; the backend is.

## Approach

A child process, for the same reason `ollama-worker.ps1` uses one — one process
serves one endpoint — but inverted. Where the worker script spawns a child
*away* from Anthropic, this spawns a child *back to* Anthropic: strip the Ollama
environment, render the caller's own session transcript to text, and pass it to
a `claude -p` run pinned to `claude-fable-5-1`.

Surface is a skill plus a script, not an MCP server. Ollama sessions run with no
`CLAUDE_CONFIG_DIR` override, so they read the global `~/.claude.json`; an MCP
server registered there would appear in every Anthropic session too, alongside
the native advisor it duplicates. There is no clean way to scope an MCP server
to Ollama-backed sessions. Skills are listed in the same system prompt the
session already receives, so discoverability is comparable, and the script stays
runnable and testable standalone.

Autonomous invocation — the model calling the advisor unprompted — is a
system-prompt problem, not a transport problem. It is solved by the SessionStart
hook below, which works identically under either surface and is therefore not a
reason to prefer MCP.

## Components

Repo directory `advisor-bridge/`, installed into `~/.claude` by `install.ps1`,
mirroring the layout `ollama-workers/` already uses.

| Path in repo | Installed to | Job |
|---|---|---|
| `scripts/fable-advisor.ps1` | `~/.claude/scripts/` | Engine: locate, render, spawn, guard, log |
| `skills/advisor/SKILL.md` | `~/.claude/skills/advisor/` | When to call, how to weigh the answer |
| `advisor-persona.md` | `~/.claude/advisor-persona.md` | The child's system prompt |
| `hooks/advisor-bridge-status.py` | `~/.claude/hooks/` | SessionStart nudge, Ollama sessions only |
| `fable-advisor.example.json` | `~/.claude/fable-advisor.json` | Config, seeded on first install only |

### Session locator

Resolve the caller's transcript by **globbing**
`<base>/projects/*/$env:CLAUDE_CODE_SESSION_ID.jsonl`, not by computing the
project directory name from the working directory. `<base>` is the caller's
`CLAUDE_CONFIG_DIR` when set, otherwise `~/.claude`. Ollama sessions do not set
it today, but honouring it costs one line and its absence would be a
wrong-file failure rather than an error. Note the asymmetry: the *child* is
launched against the default `~/.claude` regardless, because that is where the
Anthropic credential lives.

Claude Code sanitizes the cwd into that directory name (`C:\Users\<user>\...`
becomes `C--Users-<user>-...`). The rule is undocumented. Reimplementing it buys
nothing a glob does not already give, and its failure mode is a wrong-or-missing
file rather than an error.

`CLAUDE_CODE_SESSION_ID` unset, no match, or more than one match are all exit-1
errors naming the remedy. The script never falls back to "newest transcript
nearby": advising on the wrong session is worse than not advising.

### Renderer

Keep records where `type` is `user` or `assistant` **and** `isSidechain` is
`false`. Drop everything else.

This filter is doing more work than it appears. In a measured two-turn Ollama
session the file was 261 KB, of which `attachment` records — hook output,
system-reminders — were 252 KB (96.6%). The signal was 6 KB. Sending the raw
file would spend most of the budget on the caller's own hook text. The
`isSidechain` filter matters because subagent turns land in the same file and
would otherwise interleave into the narrative as if the main agent had done
them.

Per content block:

| Block | Treatment |
|---|---|
| `text` | full |
| `thinking` | capped at 600 chars |
| `tool_use` | tool name, then input capped at 800 chars |
| `tool_result` | capped at `maxToolResultChars` (default 2000) |

**A turn is one surviving JSONL record** — one `user` record or one `assistant`
record. A `tool_use` and the `tool_result` answering it are therefore two turns,
not one. Stated because "last 12 turns" and "drop middle turns" below are
otherwise implementable three different ways.

Budget enforcement, applied in this order until under `charBudget`:

1. First user message always rendered in full — it is the task, and losing it
   makes everything after it unreadable.
2. Last 12 turns rendered in full.
3. Mid-transcript `tool_result` blocks hard-capped.
4. Still over: drop middle turns oldest-first, replacing each run with
   `[N turns elided]` so the advisor can see that it is not reading everything.

A header precedes the render: cwd, git branch, the caller's model, total turn
count, and how many turns were elided.

### Child spawn

Build the child environment from **empty** using `ProcessStartInfo` with
`UseShellExecute = $false`, adding only: `PATH`, `USERPROFILE`, `HOME`, `TEMP`,
`SystemRoot`, `APPDATA`, `LOCALAPPDATA`, and `CLAUDE_EFFORT=xhigh`.

A whitelist, not a blacklist of `ANTHROPIC_*` vars to unset. A blacklist is one
Ollama release away from missing a newly-exported variable, and the symptom of
that miss is GLM answering in the advisor's voice — which reads as success.
`CLAUDE_EFFORT` is set explicitly rather than inherited so that its value is a
decision recorded here, not an accident of what the parent happened to export.

Command:

```
claude -p --model claude-fable-5-1
        --system-prompt "<contents of ~/.claude/advisor-persona.md>"
        --tools "" --strict-mcp-config --setting-sources ""
        --output-format json
```

This build has no `--system-prompt-file`; the script reads the persona file and
passes its contents as `--system-prompt`. The persona therefore stays editable
without touching code, which is the property that mattered. Do not add
`--exclude-dynamic-system-prompt-sections` — its own help text says it is
ignored whenever `--system-prompt` is passed, so it would be a flag that reads
as load-bearing while doing nothing.

Working directory `~/.claude/advisor-scratch`, created on demand. The advisor
child needs no repository access — it has no tools — and running it in the
caller's cwd would file its transcript in the caller's project directory, where
the next `claude --continue` could resume the advisor instead of the user's own
session.

No separate `CLAUDE_CONFIG_DIR`. The reason `ollama-worker.ps1` needs one does
not apply here: that worker's transcripts are produced by a non-Anthropic
backend and fail to resume against the Anthropic API, so they must be kept out
of `~/.claude`. This child's transcripts are Anthropic-produced and resumable.
Sharing `~/.claude` also keeps the live OAuth credential — including its
refresh — rather than a copy that goes stale.

The rendered transcript is passed on stdin.

`--setting-sources ""` is load-bearing beyond cost. It stops the child from
reading `~/.claude/settings.json`, so no SessionStart hook fires inside the
advisor. Measured: a child run with the flag produced a 12 KB transcript with
zero `hook_success` records; the same call without it produced 290 KB with five.
Without the flag, this project's own SessionStart nudge would fire inside the
advisor it launched.

### Persona

The highest-leverage artifact here, and the cheapest to iterate on — it is a
file, not code. A bad persona returns expensive, generic encouragement. It must
instruct the advisor to:

1. Open by classifying where the caller actually is — orienting, committing to
   an approach, stuck, or declaring done — because the useful advice differs
   completely between those.
2. Diagnose from what the caller *actually tried*, quoting the transcript, not
   from what the task sounds like it needs.
3. Give the discriminating check rather than the verdict: the command, file, or
   test that would separate two hypotheses.
4. State explicitly whether any remaining concern blocks progress or is worth
   noting and moving past — an advisor that flags everything at equal weight
   makes the caller's next decision harder, not easier.
5. Be terse and specific. The caller is a model with a budget, not a reader.

Acceptance for this file is behavioural: run it against a captured transcript of
a genuinely stuck session and check the reply names a next action, not a
summary.

### Guards

Two, both mandatory, both fail-closed:

1. **Pre-spawn.** Assert the constructed child environment contains no key
   matching `ANTHROPIC_*`. Abort before launching if it does.
2. **Post-run.** Assert the result envelope's `modelUsage` contains exactly
   `claude-fable-5-1`. Any other model means the call was answered by something
   other than the intended advisor: discard the reply, exit 2, log it.

The second guard is the one that makes this safe to build. Without it the whole
failure mode this bridge exists to prevent — GLM advising GLM — returns
silently, formatted as advice.

### Output, logging, exits

stdout carries the envelope's `result` text and nothing else. The caller is a
model reading advice, not a JSON parser.

One row per run appended to `~/.claude/fable-advisor.log.jsonl`: timestamp,
caller session id, chars sent, turns rendered, turns elided, input and output
tokens, cost, duration, verdict.

| Exit | Meaning |
|---|---|
| 0 | Advice returned |
| 1 | Wrapper error — no session id, no transcript, missing credentials, bad config, disabled |
| 2 | Advisor call failed (`is_error`, nonzero child exit, empty envelope, script-side timeout) or the model guard tripped |

The script kills the child at 240 s and exits 2. Without its own timeout the
only limit is the caller's Bash-tool timeout, which kills the wrapper too — no
exit code, no log row, and no way to tell a hung call from a slow one when
reading the log later.

### Skill

`~/.claude/skills/advisor/SKILL.md` covers when to call (before substantive
work, when stuck, when changing approach, before declaring done), and how to
weigh the answer — primary-source evidence in the caller's own transcript
outranks the advice, and a genuine conflict warrants one reconciling call rather
than a silent switch.

The invocation line **must** pass `timeout: 300000` to the Bash tool. A trivial
Fable call measured 64 s wall; a real transcript with extended thinking will
exceed the 120 s default, and the caller would see a killed call rather than
advice. This is the failure most likely to spoil first use.

### SessionStart hook

`advisor-bridge-status.py` fires only when `ANTHROPIC_BASE_URL` is set and its
host is not `api.anthropic.com`. In that case it injects the advisor protocol as
`additionalContext`. In every other session it exits silently, so sessions that
already have the native `advisor` tool are untouched.

### Config

`~/.claude/fable-advisor.json`:

```json
{
  "enabled": true,
  "model": "claude-fable-5-1",
  "charBudget": 80000,
  "maxToolResultChars": 2000
}
```

`enabled` is enforced by the script itself, not only by the skill's prose — a
call on stale context after a compact fails loudly instead of spending money. A
missing or unreadable file counts as disabled.

## Cost

Measured, not estimated. The default Claude Code system prompt is ~38 K tokens;
at Fable's rate with a 1-hour cache write, a four-token reply cost **$0.77**.
The same call with a short `--system-prompt`, `--tools ""`,
`--strict-mcp-config` and `--setting-sources ""` shrank the prompt to 1,414
tokens and cost **$0.029** — a 26× reduction in fixed overhead. The real persona
is longer than the one-line prompt used in that measurement, so budget a few
hundred tokens more.

**The transcript does not amortize.** Prompt caching matches an exact prefix,
and the rendered transcript is one user message that differs on every call — new
turns, different truncation. Only the persona in the system prompt is reused.
The spike shows the split directly: `cache_creation 1414, input 2` — the system
prompt cached, the user message did not.

So every call pays close to full price for its transcript. At `charBudget:
80000` (~20 K tokens) that is roughly **$0.20–0.40 per call**, every call, with
only the ~1.4 K persona amortized. Raising the budget to 120 K raises every call
proportionally; the log's cost column is the evidence for whether it is worth
it.

The only real lever on this is resuming one advisor session across calls
(`--resume`), so each call sends the delta rather than the whole transcript.
That is deliberately out of scope for the first version — it trades a
stateless, one-shot design for session lifecycle management — but it is the
lever, and it is named here so the cost is a known trade rather than a
discovery.

## Testing

- **Golden render.** A captured session JSONL fixture renders to expected text.
  Covers the attachment filter, the `isSidechain` filter, each block type's cap,
  and the elision path.
- **Environment scrub.** Run with `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
  and `ANTHROPIC_DEFAULT_OPUS_MODEL` set to Ollama-like values; `-DryRun` prints
  the constructed child environment; assert no `ANTHROPIC_*` key survives.
- **Model guard.** Force a non-Fable model and assert exit 2 with the reply
  discarded, not printed.
- **Locator.** Unset `CLAUDE_CODE_SESSION_ID` and assert exit 1 with a remedy;
  assert no fallback to a nearby transcript.
- **Disabled gate.** `enabled: false` exits 1 without launching a child.
- **End-to-end.** One real call from a live `ollama launch claude` session.

The golden fixture is synthesized, not a captured probe session. Real
transcripts carry absolute paths, the user's email, and machine details, and
this repo is public.

Two things for the implementer to verify rather than assume:

- Whether SessionStart fires with `source: "compact"`. The native advisor
  survives a compact because it lives in the system prompt; this bridge's
  protocol arrives as `additionalContext` and may not. If it does fire, the
  hook's matcher must not exclude it, or the protocol silently disappears
  mid-session.
- `ollama-workers/install.ps1` is the model for this install script but is not
  on `main` — read it from the `cs-wt/ollama-workers` worktree
  (`feat/ollama-workers`). Both branches install into `~/.claude`, so whichever
  merges second inherits the job of keeping the two install scripts consistent.

## Out of scope

- An MCP wrapper. The script is the engine; wrapping it later is a thin layer
  over an unchanged core, and the scoping problem above has to be solved first.
- Advisor support for any backend other than Ollama. The hook's detection is
  "base URL is not Anthropic", which happens to cover other proxies, but nothing
  else here is tested against them.
- Multi-turn conversation with the advisor. Each call is one shot over the
  current transcript, as the native tool is.
