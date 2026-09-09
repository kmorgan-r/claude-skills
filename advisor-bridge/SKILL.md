---
name: advisor-bridge
description: Reach an Anthropic advisor model from a Claude Code session whose backend is not Anthropic (ollama launch claude, GLM or Kimi). Use when the user says /advisor-bridge, asks to enable or disable the advisor bridge, asks whether the advisor is reachable from an Ollama session, wants advice on the current session from a stronger model, or asks why the built-in advisor tool is missing.
---

# Advisor bridge

Sends this session's own transcript to an Anthropic model in a separate process
and prints its advice. The built-in `advisor` tool is disabled when the backend
is not Anthropic, and would be useless if it were not: one process serves one
endpoint, so an in-process advisor would be GLM advising GLM.

State: `~/.claude/advisor-bridge.json` — `{ "enabled", "model", "charBudget",
"maxToolResultChars", "timeoutSec" }`. The wrapper enforces `enabled` itself and
exits 1 without launching anything unless it is `true`, so a call on stale
context after a compact fails loudly instead of spending money. A missing or
unreadable file counts as off.

## Calling the advisor

```
Bash(command: "pwsh -NoProfile -File ~/.claude/scripts/advisor-bridge.ps1",
     timeout: 300000)
```

**`timeout: 300000` is not optional.** A trivial call measured 64 s wall; a real
transcript with extended thinking exceeds the Bash tool's 120 s default, and the
caller sees a killed call rather than advice. This is the failure most likely to
spoil first use.

The ordering matters: the script's own 240 s kill fires first and produces an
exit code and a log row; the 300 s Bash timeout is the outer backstop. Set the
other way round, the wrapper dies before it can report anything.

## When to call

- **Before substantive work** — before writing, before committing to an
  interpretation, before building on an assumption. Orientation (finding files,
  reading what is there) is not substantive work; writing, editing and declaring
  an answer are.
- **When stuck** — errors recurring, an approach not converging, results that do
  not fit.
- **When considering a change of approach.**
- **When the task looks complete** — but make the deliverable durable first
  (write the file, commit the change). The call takes a minute; if the session
  ends during it, a durable result persists and an unwritten one does not.

On tasks longer than a few steps, call once before committing to an approach and
once before declaring done. On short reactive work where the next action is
dictated by output you just read, do not keep calling — the advisor adds most of
its value before the approach crystallizes.

## How to weigh the answer

Give it serious weight. But **primary-source evidence in your own transcript
outranks the advice**: if you followed a step and it failed empirically, or the
file says X where the advice says Y, adapt. A passing self-test is not evidence
the advice is wrong — it is evidence your test does not check what the advice
checks.

If you have already retrieved data pointing one way and the advisor points
another, do not switch silently. Make one reconciling call naming the conflict.

## Exit codes

| Exit | Meaning | What to do |
|---|---|---|
| 0 | Advice on stdout | Read it |
| 1 | The wrapper refused before spawning — disabled, no session id, no transcript, missing persona, empty render | Read the message; it names the remedy |
| 2 | The call was attempted and its result is not trustworthy — timeout, child error, or a guard tripped | Do NOT retry blindly; a model-guard trip means something other than the intended advisor answered |

A guard trip discards the reply rather than printing it. That is the point: the
failure this bridge exists to prevent — the local model answering in the
advisor's voice — otherwise returns silently, formatted as advice.

## Commands

**`status`** (also the bare invocation) — read the state file, print `enabled`,
model, charBudget and timeoutSec, then the last few rows of
`~/.claude/advisor-bridge.log.jsonl` so the user sees real cost, not the
estimate.

**`on [model]`** — set `enabled: true`. With no model, keep the stored one.

**`off`** — set `enabled: false`. Leave `model` alone so the next `on` remembers it.

Write the file with a whole-object rewrite, preserving the keys you are not
changing:

```powershell
$p = "$HOME/.claude/advisor-bridge.json"
$s = Get-Content -Raw $p | ConvertFrom-Json
$s.enabled = $true          # or $false
$s | ConvertTo-Json | Set-Content -LiteralPath $p
```

After any change, state the new setting in one line. It takes effect on the next
call — no restart needed, because this skill's text is now in context.

## Cost

Every call pays close to full price for its transcript: roughly **$0.20–0.40**,
every call. Prompt caching matches an exact prefix and the rendered transcript is
one user message that differs on every call, so only the ~1.4 K persona
amortizes. Raising `charBudget` raises every call proportionally.

Read `~/.claude/advisor-bridge.log.jsonl`'s `cost_usd` column rather than this
paragraph — it is the measurement, this is the estimate. Rows carrying
`"source": "envelope-file"` are canned test runs and cost nothing; exclude them.
