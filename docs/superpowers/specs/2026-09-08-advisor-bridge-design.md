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
a `claude -p` run pinned to an Anthropic model.

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

## Naming

One stem, `advisor-bridge`, across every artifact: repo directory, script,
skill directory, persona, hook, config and log. This mirrors how
`ollama-workers/` keeps a single stem across `ollama-worker.ps1`,
`ollama-worker.md`, `ollama-workers-status.py`, `ollama-workers.example.json`
and `ollama-workers.log.jsonl`.

Two names are deliberately **not** used:

- **`advisor`** as the installed skill directory. Claude Code already has a
  built-in tool by that exact name — the one this package exists to work around
  — and `~/.claude/skills/advisor/` would read as that tool's own skill. The
  sibling's convention is skill-dir-name == package name, so `advisor-bridge`
  it is.
- **`fable-advisor`** for the script, config and log. The model is a config key
  with a default, not a fixed property of the bridge; naming files after one
  model would make a model change a rename.

## Components

Repo directory `advisor-bridge/`, installed into `~/.claude` by `install.ps1`,
mirroring the layout `ollama-workers/` already uses. Note the repo keeps
`SKILL.md` at the package root — the `skills/<name>/` nesting exists only at
the install destination, and is built by `install.ps1`, exactly as
`ollama-workers/install.ps1` does it.

| Path in repo | Installed to | Job |
|---|---|---|
| `SKILL.md` | `~/.claude/skills/advisor-bridge/SKILL.md` | When to call, how to weigh the answer, on/off/status |
| `scripts/advisor-bridge.ps1` | `~/.claude/scripts/advisor-bridge.ps1` | Engine: locate, render, spawn, guard, log |
| `advisor-bridge-persona.md` | `~/.claude/advisor-bridge-persona.md` | The child's system prompt |
| `hooks/advisor-bridge-status.py` | `~/.claude/hooks/advisor-bridge-status.py` | SessionStart nudge, Ollama sessions only |
| `advisor-bridge.example.json` | `~/.claude/advisor-bridge.json` | Config, seeded on first install only |
| `install.ps1` | *(not copied — run from the repo)* | Places the files, seeds the config, registers the hook |
| `tests/*.Tests.ps1` | *(not copied)* | Pester suite, see Testing |

The log file `~/.claude/advisor-bridge.log.jsonl` is created by the script on
first run; the installer does not place it.

### Order of operations

The engine runs these in exactly this order. The order is normative: several of
the steps below are cheap checks that exist only because they must happen
*before* something expensive or irreversible, and an implementation that
reorders them loses the property.

1. Read config. Unreadable or absent → treated as disabled (step 2).
2. **Enabled gate.** Not `true` → exit 1, nothing else runs.
3. Resolve the `claude` executable to an absolute path → exit 1 if absent.
4. Read the persona file → exit 1 if absent, unreadable, or over the size
   preflight.
5. Locate the caller's transcript → exit 1 on unset session id, no match, or
   multiple matches.
6. Render the transcript.
7. **Non-empty check.** Zero surviving turns → exit 1, naming the transcript
   path. A full-price call over an empty render returns confident advice about
   nothing.
8. Build the child environment from empty.
9. **Pre-spawn guard** on that environment → exit 2 if it trips.
10. Create `~/.claude/advisor-bridge-scratch` if absent.
11. Spawn, with the timeout armed.
12. Classify the child's outcome, in this precedence: **timeout** beats a
    nonzero exit (a killed child also exits nonzero, and `timeout` is the more
    specific fact); then `child_error` (`is_error`, or a nonzero exit); then
    `no_envelope` (nothing on stdout parses as a result envelope). All three
    exit 2.
13. **Post-run model guard** on the envelope → exit 2 if it trips. `model_guard`
    beats `child_error` wherever both could apply, for the same reason: a reply
    from the wrong model is what the caller must not act on.
14. Write the log row.
15. Print `result` to stdout, exit 0.

The gate at step 2 sits above everything that costs money or touches the
filesystem, for the reason `ollama-worker.ps1:314` gives for its own: an
orchestrator can dispatch on stale context after a compact, so the mechanism
owns the gate, not the system prompt.

The log row is written on every exit-0 and exit-2 path — the pre-spawn guard at
step 9 included, since the exit-2 table gives it a verdict and a verdict only
exists inside a row. Exit-1 paths write none: nothing was attempted, there is no
verdict to record, and a row per disabled-gate call would swamp the cost column
`## Cost` calibrates from.

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

Three exit-1 cases, each with its own message. The script never falls back to
"newest transcript nearby": advising on the wrong session is worse than not
advising.

| Case | Message |
|---|---|
| `CLAUDE_CODE_SESSION_ID` unset | `advisor-bridge: CLAUDE_CODE_SESSION_ID is not set — this script must run inside a Claude Code session, not from a bare shell.` |
| No match | `advisor-bridge: no transcript for session <id> under <base>/projects/*/. The session may not have been written yet; send one message and retry.` |
| More than one match | `advisor-bridge: session id <id> matches N transcripts:` then one path per line, then `Rendering the wrong one would advise on someone else's session. Delete or move the stale copy, or set CLAUDE_CONFIG_DIR to disambiguate.` |

The multi-match message lists the paths because that is the only actionable
thing here — there is no correct automatic tiebreak, and "newest" is exactly the
heuristic this section refuses. A resumed session copied between config dirs is
the realistic way to reach this case, so the locator test covers it.

### Renderer

Parse the transcript **line by line, each line in its own try/catch**, skipping
any line that does not parse. Open the file share-read.

Both halves are load-bearing. The caller's own Claude Code process is appending
to this file while the wrapper reads it, so the last line is routinely a partial
record, and `$ErrorActionPreference = 'Stop'` makes an unguarded
`ConvertFrom-Json` on it a terminating error — the wrapper would die before any
log row and with an exit code outside the published table. This is the same
hazard `ollama-worker.ps1:412-424` documents for the child's own envelope, and
the same remedy.

Count skipped lines and report the count in the header, beside the elided-turn
count. A silently skipped record is indistinguishable from a record that was
never there.

Keep records where `type` is `user` or `assistant` **and** `isSidechain` is not
`true`. Drop everything else.

`-ne $true`, not `-eq $false`: a record that omits the field entirely is a main-
agent record and must be kept, and `isSidechain -eq $false` would drop it.

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
| `tool_result` | capped at `maxToolResultChars` (default 2000), **everywhere** |

The `tool_result` cap applies to every block in the render, including inside the
tail window. Twelve uncapped file reads alone exceed an 80 K budget, so a cap
that stops at the tail window is not a cap.

**A turn is one surviving JSONL record** — one `user` record or one `assistant`
record. A `tool_use` and the `tool_result` answering it are therefore two turns,
not one. Stated because "last 12 turns" and "drop middle turns" below are
otherwise implementable three different ways.

Budget enforcement, applied in this order until under `charBudget`:

1. First user message always rendered in full — it is the task, and losing it
   makes everything after it unreadable.
2. Last 12 turns rendered in full, subject to the `tool_result` cap above.
3. Drop middle turns oldest-first, replacing each run with `[N turns elided]` so
   the advisor can see that it is not reading everything.
4. **Still over: truncate the tail window itself**, oldest-first within it, down
   to the first user message plus the most recent turn, each marked
   `[truncated]`.
5. **Still over: truncate the most recent turn**, marked `[truncated]`.
6. **Still over: truncate the first user message**, marked `[truncated]`.

Steps 1 and 2 are preservation floors, not reductions; only 3 through 6 remove
text, and they run in ascending order of what it costs to lose the content —
which is why the first user message is cut last rather than first.

Steps 4–6 truncate `text` blocks, the one block type the table above renders in
full and therefore the only content no cap otherwise bounds. Without all three
the sequence has no terminal step: a first message plus twelve turns that
together exceed the budget, **or a single oversized final turn**, would ship
over budget at full per-call cost, silently. The golden-render test asserts the
final rendered length is `<= charBudget` — an assertion that is unsatisfiable
unless every block type is reachable by some truncation step.

A header precedes the render: cwd, git branch, the caller's model, total turn
count, turns elided, and lines skipped as unparseable.

### Child spawn

Build the child environment from **empty** using `ProcessStartInfo` with
`UseShellExecute = $false`. `ProcessStartInfo.Environment` is pre-populated from
the current process, so "from empty" requires an explicit `.Clear()` — it is not
the default, and the pre-spawn guard exists precisely because forgetting it is
the easy mistake.

Then add only: `PATH`, `PATHEXT`, `COMSPEC`, `USERPROFILE`, `HOME`, `TEMP`,
`SystemRoot`, `APPDATA`, `LOCALAPPDATA`, and `CLAUDE_EFFORT=xhigh`.

`PATHEXT` and `COMSPEC` are on the list because `claude` on Windows is commonly
a `.cmd` shim, and a shim launched with `UseShellExecute = $false` needs both.

A whitelist, not a blacklist of `ANTHROPIC_*` vars to unset. A blacklist is one
Ollama release away from missing a newly-exported variable, and the symptom of
that miss is GLM answering in the advisor's voice — which reads as success.
`CLAUDE_EFFORT` is set explicitly rather than inherited so that its value is a
decision recorded here, not an accident of what the parent happened to export.

**Resolve `claude` to an absolute path before spawning**, the way
`ollama-worker.ps1:66-71` resolves `ollama`: `Get-Command claude`, falling back
to the known install location, and exit 1 with a named remedy if neither
resolves. A missing binary must be a preflight blocker with a message, not a
raw spawn exception with no exit-table entry.

Command:

```
claude -p --model <config model>
        --system-prompt "<contents of ~/.claude/advisor-bridge-persona.md>"
        --tools "" --strict-mcp-config --setting-sources ""
        --output-format json
```

`--model` takes the value from config, not a literal. The same resolved value
flows into the post-run guard's comparison and into the log row, so the three
can never disagree — the pattern `ollama-worker.ps1:129` uses for its own model.

**Pass the arguments via `ProcessStartInfo.ArgumentList`, never a hand-built
`Arguments` string.** `ArgumentList` applies the CRT's quoting rules per element.
The persona is arbitrary user-editable markdown containing quotes, backslashes
and newlines, and editing it is the documented iteration loop for this project —
so this is a hazard the design actively invites the user to trigger.
`ollama-worker.ps1:344-367` spends 25 lines and a dedicated `QuoteArg` on this
same problem for two *allowlisted* short strings, and records what the naive
version did: a quote closed the argument early and the remainder became extra
flags on the child.

**Persona size preflight.** Windows caps a command line at 32,767 characters,
and the persona is the only unbounded element on it. Exit 1 if the persona
exceeds 16,000 characters, with a message naming the limit and the actual size.
A persona that long is a bug in the persona, not a case to support.

This build has no `--system-prompt-file`; the script reads the persona file and
passes its contents as `--system-prompt`. The persona therefore stays editable
without touching code, which is the property that mattered. Do not add
`--exclude-dynamic-system-prompt-sections` — its own help text says it is
ignored whenever `--system-prompt` is passed, so it would be a flag that reads
as load-bearing while doing nothing.

Working directory `~/.claude/advisor-bridge-scratch`, created on demand; exit 1 with the
path if it cannot be created. The advisor child needs no repository access — it
has no tools — and running it in the caller's cwd would file its transcript in
the caller's project directory, where the next `claude --continue` could resume
the advisor instead of the user's own session.

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

**That captured transcript is a local, uncommitted artifact.** It is not the
golden fixture and must never become one: real transcripts carry absolute paths,
the user's email and machine details, and this repo is public. Keep it outside
the repo (`%TEMP%` is fine); the acceptance run is manual and one-off.

### Guards

Two, both mandatory, both fail-closed. Both exit 2.

1. **Pre-spawn.** Assert the constructed child environment's **key set equals
   the whitelist exactly**. Abort before launching on any difference.

   Not "contains no `ANTHROPIC_*`". The Problem section above names
   `CLAUDE_CODE_SUBAGENT_MODEL` as part of the same leak, and a prefix check
   passes it untouched; so would any future `CLAUDE_*` or provider variable an
   Ollama release adds. The whitelist is already enumerated, so equality costs
   nothing and closes the whole family rather than one prefix of it.

2. **Post-run.** Assert the result envelope's `modelUsage` key set is non-empty
   and equals exactly `{<config model>}`. Any other model means the call was
   answered by something other than the intended advisor: discard the reply,
   exit 2, log it.

   *Set equality, not membership.* Membership would pass a mixed envelope, which
   is the shape a fallback or a retry against a different model produces — the
   exact case the guard is for.

The second guard is the one that makes this safe to build. Without it the whole
failure mode this bridge exists to prevent — GLM advising GLM — returns
silently, formatted as advice.

**Implementer prerequisite: capture the envelope shape first.** The spike
recorded token counts (`cache_creation 1414, input 2`) but never recorded the
`modelUsage` field's actual shape — whether it is an object keyed by model id, a
list, or nested under another key. Run one `claude -p --output-format json` call,
record the verbatim shape in this section, and write the guard against that.
Do not write the guard against an assumed shape: a guard that reads a key that
does not exist yields `$null`, and `$null -eq $null` passes. A fail-closed guard
that silently inverts to fail-open is worse than no guard, because the design
above leans on it.

### Output, logging, exits

stdout carries the envelope's `result` text and nothing else. The caller is a
model reading advice, not a JSON parser.

Two exceptions, both test seams (see `## Testing`): `-DryRun` prints its JSON
plan and exits 0 without spawning, and `-EnvelopeFile` prints a canned reply.
Neither is "advice returned" in the sense of the exit table below. An
`-EnvelopeFile` run additionally writes `"source": "envelope-file"` into its log
row, so a canned row can never be read as a billed one by the cost calibration
or by the manual end-to-end check.

One row per run appended to `~/.claude/advisor-bridge.log.jsonl`: `ts`,
`session_id` (the caller's), `model`, `chars_sent`, `turns_rendered`,
`turns_elided`, `lines_skipped`, `input_tokens`, `output_tokens`, `cost_usd`,
`duration_ms`, `verdict`.

`verdict` is one of `ok`, `timeout`, `model_guard`, `child_error`,
`no_envelope`. On any path where no envelope came back — `timeout`,
`no_envelope`, and `child_error` when the child died before writing —
`input_tokens`, `output_tokens` and `cost_usd` are `null` and `duration_ms` is
the measured wall time. Writing zeros there would make a killed call
indistinguishable from a free one in the log the cost calibration reads.

| Exit | Meaning |
|---|---|
| 0 | Advice returned |
| 1 | Wrapper error — see the table below |
| 2 | The call failed or a guard tripped — see the table below |

**Exit 1 — the wrapper refused before spawning.** Every case is detectable
locally, costs nothing, and names a remedy:

- Config missing, unreadable, or `enabled` not `true`
- `claude` executable not resolvable
- Persona file missing, unreadable, or over the size preflight
- `CLAUDE_CODE_SESSION_ID` unset
- Transcript: no match, or more than one match
- Render produced zero turns
- `~/.claude/advisor-bridge-scratch` could not be created

**Exit 2 — the call was attempted and its result is not trustworthy:**

| Case | `verdict` |
|---|---|
| Pre-spawn guard tripped | `model_guard` |
| Child killed at the timeout | `timeout` |
| Envelope reports `is_error`, or the child exited nonzero | `child_error` |
| No parseable result envelope on stdout | `no_envelope` |
| Post-run model guard tripped | `model_guard` |

Missing or expired Anthropic credentials land in `child_error`, not exit 1:
they are only discoverable from the child's own failure, and a pre-flight
credential check would duplicate the CLI's auth logic to no benefit.

The pre-spawn guard is exit 2 and not exit 1 even though nothing spawned. It is
not a configuration mistake the user can fix by editing a file — it means the
environment scrub itself is broken, which is the same class of "do not trust
this result" as the post-run guard.

**Timeout.** The script kills the child at `timeoutSec` (default 240) and exits
2. The kill must take the **process tree**: `claude` on Windows launches a node
child, and killing only the parent leaves it holding the pipe. Redirect files
are removed in a `finally`, matching the create/remove pairing at
`ollama-worker.ps1:386,410`, so a timeout does not leak temp files.

Without its own timeout the only limit is the caller's Bash-tool timeout, which
kills the wrapper too — no exit code, no log row, and no way to tell a hung call
from a slow one when reading the log later.

**240 s is a starting value, not a measurement.** The one timing datum is a
64 s wall clock for a *trivial* call; this design runs `CLAUDE_EFFORT=xhigh`
over a ~20 K-token transcript, which is a different workload. It is a config key
(`timeoutSec`) and a `-TimeoutSec` parameter override so the log's
`duration_ms` column can retune it, and so a test can drive it to 2 s against a
deliberately slow stub instead of waiting four minutes.

### Skill

`~/.claude/skills/advisor-bridge/SKILL.md` covers three things.

**When to call and how to weigh the answer.** Before substantive work, when
stuck, when changing approach, before declaring done. Primary-source evidence in
the caller's own transcript outranks the advice, and a genuine conflict warrants
one reconciling call rather than a silent switch.

**The invocation line, which must pass `timeout: 300000` to the Bash tool.** A
trivial Fable call measured 64 s wall; a real transcript with extended thinking
will exceed the 120 s default, and the caller would see a killed call rather
than advice. This is the failure most likely to spoil first use. Note the
ordering: the script's own 240 s kill fires first and produces an exit code and
a log row, and the 300 s Bash timeout is the outer backstop — the two must not
be set the other way round, or the wrapper dies before it can report.

**`on` / `off` / `status`**, mirroring `ollama-workers/SKILL.md`'s command
section, including its whole-object rewrite recipe:

```powershell
$p = "$HOME/.claude/advisor-bridge.json"
$s = Get-Content -Raw $p | ConvertFrom-Json
$s.enabled = $true          # or $false
$s | ConvertTo-Json | Set-Content -LiteralPath $p
```

Without this the enforced gate has no surface: a fresh install seeds
`enabled: false`, and nothing would document how to turn it on.

### SessionStart hook

`advisor-bridge-status.py` reads `~/.claude/advisor-bridge.json` **first** and
exits silently when the file is missing, unreadable, or `enabled` is not `true`
— the pattern at `ollama-workers-status.py:73-83`, documented in
`ollama-workers/SKILL.md` as "a disabled install costs no context".

Silent on all three branches, and that is a deliberate divergence: the sibling
prints on its unreadable-file branch (`ollama-workers-status.py:79`). Because
this hook runs its config read *before* the base-URL check, copying that print
verbatim would put a message into every Anthropic session too — the exact thing
the paragraph below forbids.

Only then does it check `ANTHROPIC_BASE_URL`: set, and its host not
`api.anthropic.com` → inject the advisor protocol as `additionalContext`.
Anything else → exit silently, so sessions that already have the native
`advisor` tool are untouched.

Both gates matter and neither substitutes for the other. Base-URL alone would
inject the protocol into every Ollama session while the bridge is off, spending
context on every session and steering the model into calls that exit 1 —
turning the bridge off would not turn its surface off.

### Config

`~/.claude/advisor-bridge.json`, seeded from `advisor-bridge.example.json` on
first install only:

```json
{
  "enabled": false,
  "model": "claude-fable-5-1",
  "charBudget": 80000,
  "maxToolResultChars": 2000,
  "timeoutSec": 240
}
```

`enabled` seeds **false**, matching `ollama-workers.example.json:2`. A fresh
install of a package that spends $0.20–0.40 per call must not be live before the
user has said so once. `/advisor-bridge on` is the opt-in.

`enabled` is enforced by the script itself, not only by the skill's prose — a
call on stale context after a compact fails loudly instead of spending money. A
missing or unreadable file counts as disabled.

Read the three numeric keys with `-as [int]`, not a cast, each with the default
above as its fallback and a positive-value floor. `ollama-worker.ps1:128-133`
records why: a non-numeric value cast under `$ErrorActionPreference = 'Stop'` is
a terminating error that takes the wrapper down before it can report what was
wrong with the config.

## Cost

Two **measurements**. The default Claude Code system prompt is ~38 K tokens; at
Fable's rate with a 1-hour cache write, a four-token reply cost **$0.77**. The
same call with a short `--system-prompt`, `--tools ""`, `--strict-mcp-config`
and `--setting-sources ""` shrank the prompt to 1,414 tokens and cost **$0.029**
— a 26× reduction in fixed overhead. The real persona is longer than the
one-line prompt used in that measurement, so budget a few hundred tokens more.

**The transcript does not amortize.** Prompt caching matches an exact prefix,
and the rendered transcript is one user message that differs on every call — new
turns, different truncation. Only the persona in the system prompt is reused.
The spike shows the split directly: `cache_creation 1414, input 2` — the system
prompt cached, the user message did not.

**A derived estimate, not a measurement.** At `charBudget: 80000` (~20 K tokens)
every call pays close to full price for its transcript: roughly **$0.20–0.40 per
call**, every call, with only the ~1.4 K persona amortized. That range is
arithmetic from the char budget, not an observed figure — no full-transcript
call has been billed yet. The log's `cost_usd` column replaces it after the
first runs, and is the evidence for whether raising `charBudget` to 120 K is
worth the proportional increase.

The only real lever on this is resuming one advisor session across calls
(`--resume`), so each call sends the delta rather than the whole transcript.
That is deliberately out of scope for the first version — it trades a
stateless, one-shot design for session lifecycle management — but it is the
lever, and it is named here so the cost is a known trade rather than a
discovery.

## Install

`install.ps1` at the package root, `-DryRun` supported, mirroring
`ollama-workers/install.ps1`: idempotent file copies, seed-if-absent for the
JSON config, and one `SessionStart` entry added to `~/.claude/settings.json`
after a timestamped backup, with the rewrite verified and rolled back if
anything moved.

**One deliberate divergence, and it is the reason this section exists.** This
will be the first time two packages in this repo append to the same
`SessionStart` category. `ollama-workers/install.ps1:128-134` skips comparing
that category wholesale and then confirms only that *its own* entry landed:

```powershell
# SessionStart is the one we appended to, so it must differ.
if ($cat -eq 'SessionStart') { continue }
...
$written = @($after.hooks.SessionStart) | ForEach-Object { $_.hooks } |
    Where-Object { $_.command -like '*ollama-workers-status*' }
if (-not $written) { $lost.Add('SessionStart entry was not written') }
```

A mirrored installer that copied this verbatim would check only for
`*advisor-bridge-status*`, so a rewrite that dropped the ollama-workers entry
would verify clean and keep the damaged file. The writer at
`install.ps1:83-85` is correct — it copies existing groups — but the verifier
cannot detect its own failure here.

So: **collect every `SessionStart` command string before the rewrite, and assert
each one is still present afterwards**, in addition to asserting the new entry
landed. Roll back to the backup on any loss.

`-DryRun` prints that collected set as JSON — every pre-existing `SessionStart`
command string, plus the entry it would add — and exits without writing, in the
shape `ollama-worker.ps1:373-384` uses. The sibling's dry run emits only a step
line, so a verbatim mirror would leave the install test's preservation assertion
with no artifact to read.

**Back-fill the same check into `ollama-workers/install.ps1`** in this change.
The two are symmetric hazards, and whichever installer is run second is the one
that can destroy the other's entry — fixing only the new one leaves half the
failure live.

`README.md` gets an index-table row for `advisor-bridge` and a "Notes per skill"
entry, matching the shape of the `ollama-workers` entries: Windows-only
(PowerShell 7), the `claude` CLI and an Anthropic login as prerequisites,
`./advisor-bridge/install.ps1` (`-DryRun` first) as the install command, and
off-by-default stated explicitly.

## Testing

**Runner: Pester 5**, suite at `advisor-bridge/tests/`, one file per area:
`Render.Tests.ps1`, `Locator.Tests.ps1`, `Env.Tests.ps1`, `Guard.Tests.ps1`,
`Config.Tests.ps1`, `Install.Tests.ps1`. Run with:

```powershell
Invoke-Pester advisor-bridge/tests -Output Detailed
```

That command goes in `README.md` as this package's `**Tests:**` line, matching
how `find-cold-leads` documents its pytest command. Every test below is offline,
deterministic, and spends nothing. Pester is the choice because the repo has no
PowerShell test framework yet and this is the only PowerShell package that will
ship one; a plain `.ps1` harness with `exit 1` would work equally and can be
substituted at plan time, but the suite must have *a* named runner and a
documented command — not a list of intentions.

Four test seams are **deliverables of the script**, not test-only afterthoughts,
and are specified here because several of the tests below cannot exist without
them:

| Seam | What it does |
|---|---|
| `-DryRun` | Prints the constructed child environment, the resolved argument list, the cwd and the rendered char count as JSON, then exits 0 **before spawning**. Mirrors `ollama-worker.ps1:42,373-384`. |
| `-EnvelopeFile <path>` | Reads a canned result envelope from a file instead of spawning `claude`. The only way to drive the post-run guard against a wrong model without a real, non-deterministic API call. |
| `-TimeoutSec <n>` | Overrides `timeoutSec`, so the timeout path is testable in 2 s against a slow stub. |
| `-ClaudeHome <path>` | Overrides the `~/.claude` base for the config, log, persona and scratch paths. Without it the config, guard, timeout and log tests all read the developer's live config — which seeds `enabled: false`, so each exits 1 at step 2 before reaching the behaviour under test — and append to the real log `## Cost` calibrates from. Running the script in a child `pwsh` with `USERPROFILE` overridden is not a substitute: `$HOME` and `~` resolve once in PowerShell and do not follow a mid-process change. |

### Cases

**Render** — golden fixtures, each asserting exact expected text:

- Attachment filter: `attachment` records dropped, `user`/`assistant` kept.
- Sidechain filter: `isSidechain: true` dropped; **a record with no
  `isSidechain` field at all is KEPT** (the `-ne $true` rule).
- Per-block caps: `thinking` at 600, `tool_use` input at 800, `tool_result` at
  `maxToolResultChars`, asserted both mid-transcript and **inside the last 12
  turns**. The cap applies everywhere, and only the tail-window case proves it.
- Elision: `[N turns elided]` appears with the right N.
- **First-message independence**: a fixture long enough that the first user
  message falls *outside* the last-12-turns window, asserting it still renders
  in full. Without this, an implementation that folds rule 1 into rule 2 passes
  every other render test.
- **Termination**, two fixtures: one whose first message plus twelve turns alone
  exceed `charBudget`, one whose single most recent turn does. Each asserts the
  final render is `<= charBudget` and carries `[truncated]`, so the budget
  sequence is shown to terminate from either end.
- **Truncated final line**: a fixture whose last line is a half-written JSON
  record, asserting the render succeeds and the header reports one skipped line.
- **Attachment saving, scoped to what is specified**: assert what the `type`
  filter actually does — `attachment` records go — and nothing more. The 96.6%
  figure is measured against that filter alone. Whether hook output *also* rides
  inside surviving `user` records is an open question listed under *For the
  implementer to verify*, and until it is settled a stripping assertion would
  test a behaviour no rule in `### Renderer` specifies.
- **Empty render**: a transcript of nothing but attachments, asserting exit 1
  naming the transcript path, with no child spawned.

Fixtures are **synthesized, not captured**. Real transcripts carry absolute
paths, the user's email, and machine details, and this repo is public. To make
that actionable, the plan's first render task must begin by capturing one real
record of each kind, recording the field shapes (not the content) in a short
`tests/fixtures/SCHEMA.md`, and synthesizing from that. A fixture invented from
this document's prose would validate the renderer against a schema that is not
the one Claude Code writes.

**Locator** — all three exit-1 paths, each asserting the message names its own
remedy: session id unset; session id with zero matches; session id matching
transcripts under two different `<base>/projects/*/` directories. Assert in
every case that no fallback to a nearby transcript occurs.

**Config / disabled gate** — three cases, each asserting exit 1 with no child
spawned: `enabled: false`; config file absent; config file present but invalid
JSON. The last two are the cases `### Config` calls out by name. Plus:
non-numeric and negative `charBudget`, `maxToolResultChars` and `timeoutSec` each
fall back to their default rather than throwing.

**Environment scrub** — run with `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_DEFAULT_OPUS_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` set to
Ollama-like values; `-DryRun` prints the constructed child environment; assert
the printed key set **equals the whitelist exactly** — not merely that no
`ANTHROPIC_*` survived, which would pass while `CLAUDE_CODE_SUBAGENT_MODEL`
leaked.

**Model guard** — `-EnvelopeFile` with a canned envelope whose `modelUsage`
names a non-configured model: assert exit 2, `verdict: "model_guard"`, a log row
written, and **nothing printed to stdout**. Then a canned envelope with *two*
models including the right one: assert it also trips (set equality, not
membership). Then a canned envelope with `modelUsage` absent entirely: assert it
trips rather than passing on a `$null` comparison.

**Timeout** — `-TimeoutSec 2` against a stub that sleeps 10: assert exit 2,
`verdict: "timeout"`, a log row with `null` token/cost fields and a real
`duration_ms`, no orphaned child process, and no leftover temp files.

**Log row** — assert the appended JSONL line parses and carries all twelve
documented fields on both the success and the timeout paths.

**Install** — `-DryRun` against a fixture `settings.json` that already contains
an `ollama-workers-status` SessionStart entry: assert the plan preserves it.
Then a real run under `-ClaudeHome <temp dir>`, asserting both entries are
present afterwards and that a simulated loss triggers the rollback.

**Skill invocation timeout** — grep `SKILL.md` for `timeout: 300000`. Trivial,
static, and guards the failure the spec itself calls most likely to spoil first
use.

### Manual, not in the suite

**End-to-end.** One real call from a live `ollama launch claude` session.
Assertions: exit 0; `result` text printed to stdout; a log row whose `verdict`
is `ok` and whose `model` is the configured one; and the child's own transcript
under `~/.claude/projects/*/` carries **zero `hook_success` records**, which is
the only check that would catch `--setting-sources ""` silently ceasing to
suppress hooks.

This test spends real money on every run (see `## Cost`) and needs a live Ollama
session, so it is **excluded from the Pester suite and from any automated test
glob**. `ship`'s P4 exit gate runs "the change's own test files"; an end-to-end
file matching `*.Tests.ps1` would bill every pipeline run. Keep it as
`advisor-bridge/tests/manual/e2e.md` — a documented procedure, not an
executable test.

**Persona acceptance.** Behavioural, against a local uncommitted transcript, per
`### Persona`.

### For the implementer to verify

- **The `modelUsage` envelope shape**, before writing the post-run guard. See
  `### Guards` — this is a prerequisite, not a nice-to-have.
- **Whether hook output and system-reminders also ride inside surviving `user`
  records**, not only in `attachment` records. One grep of a real transcript
  settles it. If they do, an explicit stripping rule belongs in `### Renderer`
  first — naming the delimiters, and whether the removed span counts toward
  `chars_sent` — and a golden case second. As specified the renderer has three
  rules, and none of them removes anything from inside a `text` block.
- **Whether SessionStart fires with `source: "compact"`.** The native advisor
  survives a compact because it lives in the system prompt; this bridge's
  protocol arrives as `additionalContext` and may not. If it does fire, the
  hook's matcher must not exclude it, or the protocol silently disappears
  mid-session.

`ollama-workers/install.ps1` is the model for this install script and is on
`main`. Read it there. Both installers write into `~/.claude`, so this one must
stay consistent with it on idempotence and on seeding the JSON config only when
absent. On the `SessionStart` verification it must diverge from that file *as it
stands on `main` today* — and `## Install` requires back-filling the same check
there in this change, after which the two converge again.

## Out of scope

- An MCP wrapper. The script is the engine; wrapping it later is a thin layer
  over an unchanged core, and the scoping problem above has to be solved first.
- Advisor support for any backend other than Ollama. The hook's detection is
  "base URL is not Anthropic", which happens to cover other proxies, but nothing
  else here is tested against them.
- Multi-turn conversation with the advisor. Each call is one shot over the
  current transcript, as the native tool is.
