# End-to-end check (manual — spends real money)

**Not a `*.Tests.ps1` file, deliberately.** `ship`'s P4 exit gate runs "the change's
own test files"; an end-to-end matching that glob would bill every pipeline run. This
is a procedure a human runs once after install and after any change to the spawn path.

Cost: this procedure makes two full-size calls, roughly $0.20–0.40 each depending on
transcript size — the main run below, and a second run in Assertion 4 against a
different kind of transcript — plus one much smaller call in the envelope-shape check
(a trivial prompt, a few cents), which only needs to be repeated after a `claude` CLI
upgrade, not on every pass of this procedure.

## Setup

```powershell
pwsh -NoProfile -File advisor-bridge/install.ps1
# turn it on
$p = "$HOME/.claude/advisor-bridge.json"
$s = Get-Content -Raw $p | ConvertFrom-Json; $s.enabled = $true
$s | ConvertTo-Json | Set-Content -LiteralPath $p
```

Start a session on the non-Anthropic backend, do a few turns of real work, then:

```powershell
pwsh -NoProfile -File "$HOME/.claude/scripts/advisor-bridge.ps1"
```

## Assumption check: does the envelope really arrive on one line?

The whole spawn-path parse in `advisor-bridge.ps1` rests on an assumption the
automated suite cannot check: that `claude -p --output-format json` writes its result
envelope as a single line of stdout. The suite's `-EnvelopeFile` seam feeds the parser
a canned single-line envelope, which pins the code but not the assumption — a
multi-line envelope would still deserialize correctly once read as one joined string,
so confirming "it parsed" proves nothing about how the real CLI writes it. The wrapper
also never surfaces the child's raw stdout (it keeps only the parsed envelope), so this
cannot be checked from inside the run above without editing the script — it needs its
own small, separate call.

Run once, and again after upgrading the `claude` CLI, with a trivial prompt so it costs
a few cents rather than the full transcript price:

```powershell
$cfg = Get-Content -Raw "$HOME/.claude/advisor-bridge.json" | ConvertFrom-Json
$raw = "reply in one word" | & claude -p --model $cfg.model --output-format json --strict-mcp-config --setting-sources ''
@($raw).Count   # expect 1
```

`@($raw)` is what makes this a real check: PowerShell hands back one call's stdout as
a bare string when it is a single line and as an array when it is more than one, so
wrapping in `@()` before counting is what actually distinguishes "one line" from
"many." Skipping the wrapper — or just confirming `$raw | ConvertFrom-Json` succeeds —
would pass either way and prove nothing.

If the count comes back above 1, run the wrapper's own extraction to tell apart a
harmless extra line (a stray warning printed before a genuinely single-line envelope)
from the assumption actually being false:

```powershell
$line = @($raw) | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
($line | ConvertFrom-Json).type   # expect 'result'
```

If this still prints `result`, the extra lines were noise the wrapper's own filter
already discards safely — nothing to fix. If it throws or prints anything else, the
envelope itself spans more than one line and the assumption is genuinely false.

**If the assumption is genuinely false:** the
spawn-path parse — the line that splits the child's stdout on newlines and keeps only
the last line that starts with a curly brace — needs revisiting. That is a change to
`advisor-bridge.ps1`, not a config change.

## Assertions

1. **Exit 0** and advice text on stdout — not JSON, not an empty line.
2. **The log row is right.** Last line of `~/.claude/advisor-bridge.log.jsonl`:
   `verdict` is `ok`, `model` is the configured model, `cost_usd` is non-null, and
   there is **no** `source` key (a `source` of `envelope-file` would mean a canned
   run, not a billed one).
   ```powershell
   Get-Content "$HOME/.claude/advisor-bridge.log.jsonl" | Select-Object -Last 1 | ConvertFrom-Json
   ```
3. **No hooks fired inside the child.** This is the only check that catches
   `--setting-sources ""` silently ceasing to suppress them — without it, this
   project's own SessionStart nudge would fire inside the advisor it launched.
   ```powershell
   $child = Get-ChildItem "$HOME/.claude/projects/*advisor-bridge-scratch*/*.jsonl" |
       Sort-Object LastWriteTime | Select-Object -Last 1
   (Select-String -Path $child -Pattern 'hook_success' | Measure-Object).Count
   ```
   Expected: **0**. A non-zero count means the child is reading `settings.json`.
4. **The reply passes the persona's actual review bar** — not the vaguer "the advice
   is advice." Run the call above twice: once against a transcript of a genuinely
   stuck session, once against a transcript where the caller's work is genuinely
   correct and complete. Neither transcript belongs in this repo — keep each under
   `$env:TEMP`, never commit one, and never let one become a test fixture; a real
   transcript carries absolute paths, an email address, and machine details, and this
   repo is public.

   Judge each reply against the Task 8 reviewer's actual criteria:
   - Does the required output shape **force a position**? ("Be honest" is cheap; the
     shape has to make fence-sitting impossible.)
   - Are the four moves genuinely **distinct** in the reply, or did they collapse into
     one?
   - Did it **cite transcript evidence** — quote the command, the error, the file —
     rather than generic advice that would fit any session?
   - Move 3's discriminating check: does it name a check where **"if it prints Y the
     cause is A,"** rather than a hedged verdict?
   - Move 4's blocking / non-blocking split — **the softest of the four**, satisfiable
     with vague bucket labels. Judge whether it names something concrete (a file, a
     line, a specific risk) rather than a generic bucket.
   - **On the correct-and-complete transcript, does it say so in one line and stop?**
     Ruling 29 gave the persona explicit licence to report nothing wrong; a persona
     that must find fault will invent one. If the reply manufactures a concern instead,
     that is a persona defect, not a script bug — **edit `advisor-bridge-persona.md`,
     not the script.**

   Also watch raw reply length against "be terse": a long-but-well-organised reply that
   still technically obeys that instruction is the signal to add a hard length cap to
   the persona.

## Compact-trigger check (informational — no code change either way)

One question the plan could not settle without an installed hook: does Claude Code
fire `SessionStart` with `source: "compact"`? The `SessionStart` entry `install.ps1`
registers carries no matcher on `source`, so it either fires on every reason,
compact included, or it does not — this can only be observed running for real, not
read out of the code.

Over ordinary use of an enabled, installed bridge, notice whether the advisor-bridge
protocol nudge (the text `advisor-bridge-status.py` prints) reappears in a session's
context right after an auto-compact or a manual `/compact`. Observing this
opportunistically, rather than forcing a compact solely for this check, is fine.

**Whatever you observe, record it here and make no code change.** If the nudge does
fire on compact, that is intended, not a bug to fix: the hook has no matcher to
exclude it, and a repeated nudge costs a little context, not money, since `enabled`
is still checked before the wrapper spawns anything. This is the only place in the
plan this question is ever asked; once observed, treat it as answered for good.

## If it fails

| Symptom | Look at |
|---|---|
| Exit 1, "disabled or unreadable config" | `~/.claude/advisor-bridge.json` — the seed is `enabled: false` |
| Exit 1, "no transcript for session" | The session had not been written yet; send a message and retry |
| Exit 2, `model_guard` | Something other than the intended advisor answered. Check `modelUsage` in a raw `claude -p --output-format json` call — the guard's shape assumption may be wrong |
| Exit 2, `timeout` | Raise `timeoutSec`; check `duration_ms` in the log row for how close it was |
| Envelope-shape check's follow-up prints anything but `result` (not just the line count above 1) | The single-line assumption behind the spawn-path parse in `advisor-bridge.ps1` is false; fix the parse, not the config |
| Killed with no exit code at all | The Bash tool's timeout fired before the script's. The invocation must pass `timeout: 300000` |
