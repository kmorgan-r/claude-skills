# Advisor Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Claude Code session running on a local Ollama endpoint reach an Anthropic model for advice, by rendering the caller's own session transcript and passing it to a `claude -p` child process pinned to Anthropic.

**Architecture:** A PowerShell 7 engine script does five things in a fixed order — read config and enforce an enabled gate, locate and render the caller's session JSONL, build a child environment from empty, spawn `claude -p` against Anthropic, and guard the reply's model before printing it. A skill teaches the caller when to call it; a SessionStart hook injects that protocol only into Ollama-backed sessions; an installer places the pieces and registers the hook. The whole package mirrors `ollama-workers/`, which solves the inverse problem (spawning a child *away* from Anthropic).

**Tech Stack:** PowerShell 7 (`pwsh`), Pester 5 for tests, Python 3 for the SessionStart hook, the `claude` CLI, Windows-only.

**Spec:** `docs/superpowers/specs/2026-09-08-advisor-bridge-design.md`

## Global Constraints

These apply to every task. Every task's requirements implicitly include this section.

- **Windows + PowerShell 7.** Every `.ps1` starts with `#Requires -Version 7` and `$ErrorActionPreference = 'Stop'`, matching `ollama-workers/scripts/ollama-worker.ps1:1,46`.
- **Pester 5 must be installed first.** The machine currently has **Pester 3.4.0** (the Windows-bundled version), whose API is incompatible — `Should Be` not `Should -Be`, no `BeforeAll` scoping, no `-Output Detailed`. Task 1 installs Pester 5 and every test file begins `#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }`.
- **One name stem: `advisor-bridge`.** Script, skill directory, persona, hook, config, log and scratch directory all use it. Never `advisor` (collides with Claude Code's built-in tool) and never `fable-advisor` (the model is a config key).
- **Repo layout mirrors `ollama-workers/`:** `SKILL.md` at the package root; the `skills/<name>/` nesting is built by `install.ps1` at the install destination only.
- **Fail-closed everywhere.** A check that cannot be evaluated fails the run. Never fall back to a default that spends money or sends the wrong transcript.
- **Exit codes:** `0` advice returned; `1` wrapper refused before spawning; `2` the call was attempted and its result is not trustworthy. Exit-0 and exit-2 paths write a log row; exit-1 paths write none.
- **Never commit a real transcript.** This repo is public and real transcripts carry absolute paths, the user's email and machine details. Fixtures are synthesized from recorded field *shapes*.
- **Every commit** ends with the repo's trailer:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```
  The `git commit -m "…"` line shown in each task's commit step is **shorthand for the
  subject only**. Append the trailer to every one of them — e.g. with a second `-m`, or
  a heredoc. A task executed literally as written would otherwise produce a commit that
  violates this section, in all eleven commit steps.

## File Structure

**New package `advisor-bridge/`:**

| File | Responsibility |
|---|---|
| `SKILL.md` | When to call the advisor, how to weigh the answer, `on`/`off`/`status` |
| `scripts/advisor-bridge.ps1` | The whole engine: config, locate, render, spawn, guard, log |
| `advisor-bridge-persona.md` | The child's system prompt |
| `hooks/advisor-bridge-status.py` | SessionStart nudge, Ollama sessions only |
| `advisor-bridge.example.json` | Config seed (`enabled: false`) |
| `install.ps1` | Places files, seeds config, registers the SessionStart hook |
| `tests/fixtures/SCHEMA.md` | Recorded JSONL record shapes the fixtures are built from |
| `tests/fixtures/*.jsonl` | Synthesized transcripts |
| `tests/Config.Tests.ps1` | Config read, enabled gate, numeric coercion |
| `tests/Locator.Tests.ps1` | The three locator exit-1 paths |
| `tests/Render.Tests.ps1` | Filters, block caps, budget enforcement, header |
| `tests/Env.Tests.ps1` | Child environment construction and the pre-spawn guard |
| `tests/Guard.Tests.ps1` | Model guard, timeout, envelope classification, log rows |
| `tests/Hook.Tests.ps1` | SessionStart hook gating |
| `tests/Skill.Tests.ps1` | Static assertions about `SKILL.md` |
| `tests/Install.Tests.ps1` | Installer file placement and SessionStart preservation |
| `tests/manual/e2e.md` | The one paid end-to-end procedure — documentation, not a test file |

The spec names six test files "one file per area"; this plan adds `Hook.Tests.ps1` and `Skill.Tests.ps1` as two more areas, same convention. **No test file may be named so that a `*.Tests.ps1` glob picks up a paid test** — the end-to-end lives in `tests/manual/` as markdown.

**Modified:**

| File | Change |
|---|---|
| `ollama-workers/install.ps1` | Back-fill the strengthened `SessionStart` verifier, guard its null-`hooks` crash, and add a `-ClaudeHome` seam so the change is testable (Task 11) |
| `README.md` | Index-table row and a "Notes per skill" entry for `advisor-bridge` |

**Single-file engine, deliberately.** `ollama-worker.ps1` is 481 lines in one file and this one will be comparable. Splitting it would mean dot-sourcing across files, which complicates both the install (one more copy target that can go missing) and the tests (which invoke the script as a process). The package follows the sibling's shape.

---

### Task 1: Test harness, recorded schema, and synthesized fixtures

Nothing else can be written test-first until Pester 5 runs and the fixtures exist. This task also settles one open question the spec flags for the implementer.

**Files:**
- Create: `advisor-bridge/tests/fixtures/SCHEMA.md`
- Create: `advisor-bridge/tests/fixtures/basic.jsonl`
- Create: `advisor-bridge/tests/fixtures/sidechain.jsonl`
- Create: `advisor-bridge/tests/fixtures/caps.jsonl`
- Create: `advisor-bridge/tests/fixtures/long.jsonl`
- Create: `advisor-bridge/tests/fixtures/oversized-tail.jsonl`
- Create: `advisor-bridge/tests/fixtures/truncated.jsonl`
- Create: `advisor-bridge/tests/fixtures/empty.jsonl`
- Create: `advisor-bridge/tests/Harness.Tests.ps1`

**Interfaces:**
- Consumes: nothing.
- Produces: `tests/fixtures/*.jsonl` paths used by Tasks 3–5; `SCHEMA.md` as the authority for record shape.

- [ ] **Step 1: Install Pester 5**

The machine has Pester 3.4.0, which cannot run the syntax this plan uses.

```powershell
Install-Module Pester -MinimumVersion 5.5.0 -Scope CurrentUser -Force -SkipPublisherCheck
Import-Module Pester -MinimumVersion 5.0
Get-Module Pester | Select-Object Name, Version
```

Expected: `Pester 5.x.x`. If `Install-Module` fails for lack of network or policy, STOP and report — every later task's test step depends on this.

- [ ] **Step 2: Write the harness smoke test**

`advisor-bridge/tests/Harness.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

Describe 'test harness' {
    It 'runs Pester 5' {
        (Get-Module Pester).Version.Major | Should -BeGreaterOrEqual 5
    }
    It 'can see the fixtures directory' {
        Join-Path $PSScriptRoot 'fixtures' | Should -Exist
    }
}
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: FAIL — the `fixtures` directory does not exist yet.

- [ ] **Step 4: Record the real record shapes**

Read one real transcript and record *shapes only* — key names, nesting, types. Never copy content.

```powershell
$id = $env:CLAUDE_CODE_SESSION_ID
$f  = Get-Item "$HOME/.claude/projects/*/$id.jsonl"
# One example of each distinct `type`, keys only:
Get-Content $f -TotalCount 400 | ForEach-Object {
    try { $r = $_ | ConvertFrom-Json } catch { return }
    [pscustomobject]@{ type = $r.type; keys = ($r.PSObject.Properties.Name -join ',') }
} | Group-Object type | ForEach-Object { $_.Group[0] }
```

Then, for a `user` and an `assistant` record, the content-block shapes:

```powershell
Get-Content $f | ForEach-Object {
    try { $r = $_ | ConvertFrom-Json } catch { return }
    if ($r.type -in 'user','assistant' -and $r.message.content -is [array]) {
        $r.message.content | ForEach-Object { $_.type }
    }
} | Sort-Object -Unique
```

Write the findings to `advisor-bridge/tests/fixtures/SCHEMA.md` as a table: record `type` values seen, where `isSidechain` sits, where the content blocks sit (`message.content[]` vs `content[]`), and the key names inside each block type (`text`, `thinking`, `name`/`input`, `content`). **If the nesting differs from `message.content[]`, every code block in Tasks 4 and 5 must be adjusted to match — the schema on disk wins over this plan.**

- [ ] **Step 5: Confirm the settled open question — does hook text ride inside `user` records?**

The spec lists this under *For the implementer to verify*. **It was measured against
a real 216-assistant-turn transcript before this plan was written, and the answer is
NO.** Every `<system-reminder>`, hook payload, `gitStatus`, `userEmail` and
environment block lived in its own `attachment` record — `attachment.type` values
`hook_success`, `hook_additional_context`, `environment`, `session_context`,
`total_tokens_reminder` — and **zero** of the 119 `user` records carried one inside
`message.content`. The `type` filter alone accounts for the saving, and no stripping
rule is needed.

Re-confirm on this machine, since a Claude Code upgrade could change the layout. The
grep must read **only `message.content` text**, never the serialized record: a
record's `toolUseResult` routinely quotes file contents that themselves mention
`system-reminder`, and grepping the whole record returns false positives (it did, on
the first attempt at this measurement).

```powershell
$hit = 0
Get-Content $f | ForEach-Object {
    try { $r = $_ | ConvertFrom-Json } catch { return }
    if ($r.type -ne 'user') { return }
    $texts = @()
    if ($r.message.content -is [string]) { $texts += $r.message.content }
    else { foreach ($b in $r.message.content) { if ($b.type -eq 'text') { $texts += $b.text } } }
    foreach ($t in $texts) { if ($t -match '<system-reminder>') { $hit++; break } }
}
"user records carrying <system-reminder> inside their own text: $hit"
```

Record the count in `SCHEMA.md` under a heading `## Does hook output ride inside user records?`, together with the `attachment.type` values seen.

- **Count is 0** — the expected result. Note it and move on.
- **Count is > 0** → STOP and report. The record layout changed since this was
  measured. A stripping rule must be added to the spec's `### Renderer` (naming the
  delimiters and whether the removed span counts toward `chars_sent`) before Task 4
  is written, and a golden case added here. Do not invent the rule inside the
  implementation.

- [ ] **Step 6: Synthesize the fixtures**

Build each from the recorded shape. Content is invented; only structure is copied. Use a helper so the seven files stay consistent:

```powershell
# advisor-bridge/tests/fixtures/build.ps1 — run once, output committed
#
# $side is deliberately UNTYPED and three-state: $true, $false, or $null meaning
# "omit the key entirely". The omitted-key record is the only thing that proves
# the renderer's rule is `-ne $true` rather than `-eq $false`, and a [bool]
# parameter cannot express it - $null would coerce to $false and write the key.
#
# $content is likewise untyped so a fixture can carry a bare STRING as
# message.content, not only a block array. Claude Code writes plain-string
# content for ordinary typed user messages, and Format-Turn has a dedicated
# branch for it; an [array] parameter would make that branch untestable.
function Rec([string]$type, $content, $side = $null) {
    $rec = [ordered]@{ type = $type }
    if ($null -ne $side) { $rec['isSidechain'] = [bool]$side }
    $rec['message'] = [ordered]@{ role = $type; content = $content }
    return ($rec | ConvertTo-Json -Depth 20 -Compress)
}
function Text($s)        { [ordered]@{ type = 'text';        text = $s } }
function Think($s)       { [ordered]@{ type = 'thinking';    thinking = $s } }
function Use($n, $i)     { [ordered]@{ type = 'tool_use';    name = $n; input = @{ cmd = $i } } }
function Res($s)         { [ordered]@{ type = 'tool_result'; content = $s } }
```

The seven fixtures. **The "Must contain" column is not decoration — Tasks 4 and 5
grep for these exact strings, and a fixture synthesized without them fails tests
that look correct.** Every marker sits in a `text` block, so a filter that wrongly
kept the record would render it.

| Fixture | Contents | Must contain (later tasks grep these) |
|---|---|---|
| `basic.jsonl` | one `user`, one `assistant`, one `attachment` record. The attachment is shaped like a turn — `message.content` with a `text` block — so that dropping it is proved by the filter, not by the record being unrenderable | `ATTACHMENT-MARKER` in the attachment record's text, and nowhere else |
| `sidechain.jsonl` | one `user` with `isSidechain: true` (dropped), one **with the key absent entirely** (kept), one with `false` (kept) | `SIDECHAIN-MARKER` in the `isSidechain: true` record's text, and nowhere else |
| `caps.jsonl` | `thinking` of 900 chars, `tool_use` input of 1200 chars, `tool_result` of 5000 chars — one pair mid-transcript and one inside the last 12 turns | — |
| `long.jsonl` | 40 turns, the first user message ~500 chars, each later turn ~500 chars — long enough that the first message falls outside the last-12 window. **The first user record's `message.content` is a bare STRING, not a block array** — that is the shape Claude Code writes for an ordinary typed user message, so the record the budget sequence works hardest to preserve is also the one that exercises `Format-Turn`'s string branch | `FIRST-MESSAGE-MARKER-END` as the **last** characters of the first user message's text, so its survival proves the message was kept whole rather than head-truncated |
| `oversized-tail.jsonl` | 3 turns, the most recent a single `text` block of 200,000 chars | — |
| `truncated.jsonl` | two valid records, then a third line cut mid-object (no closing brace) | — |
| `empty.jsonl` | three `attachment` records and nothing else | — |

The envelope fixtures in Task 7 carry their own marker, `SECRET-ADVICE-BODY`; they are written literally there.

- [ ] **Step 7: Run the harness test to verify it passes**

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: PASS, 2 tests.

- [ ] **Step 8: Commit**

```bash
git add advisor-bridge/tests
git commit -m "test(advisor-bridge): Pester 5 harness, recorded schema, synthesized fixtures"
```

---

### Task 2: Config, the enabled gate, and the `-ClaudeHome` seam

The gate sits above everything that costs money, so it is built first and everything later is added below it.

**Files:**
- Create: `advisor-bridge/scripts/advisor-bridge.ps1`
- Create: `advisor-bridge/advisor-bridge.example.json`
- Test: `advisor-bridge/tests/Config.Tests.ps1`

**Interfaces:**
- Consumes: nothing.
- Produces: `advisor-bridge.ps1` accepting `-ClaudeHome <path>`, `-DryRun`, `-EnvelopeFile <path>`, `-TimeoutSec <int>`; a `Get-PositiveInt($value, [int]$default)` helper; `$cfg` with keys `enabled`, `model`, `charBudget`, `maxToolResultChars`, `timeoutSec`; a `Fail([string]$message, [int]$code = 1)` helper writing `advisor-bridge: <message>` to stderr.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Config.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Script = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'

    # The parameter is -BridgeHome, NOT -Home. `$HOME` is a PowerShell automatic
    # variable with Options `ReadOnly, AllScope`, and AllScope propagates it into
    # every child scope - so a parameter named `$Home` cannot be bound. Every
    # call would throw "Cannot overwrite variable Home because it is read-only
    # or constant" at parameter binding, before the body ever runs.
    function Invoke-Bridge {
        param([string]$BridgeHome, [string[]]$ExtraArgs = @())
        # CLAUDE_CONFIG_DIR and CLAUDE_CODE_SESSION_ID are pinned to throwaway
        # values, exactly as in every other cross-process helper in this suite.
        # Leaving them ambient is not merely untidy: from Task 3 onward a run
        # that reaches the locator inherits the AMBIENT session id, and an agent
        # implementing this plan runs the suite from inside a live Claude Code
        # session whose own transcript sits at precisely the path the locator
        # globs. `-DryRun` would then print the developer's real transcript -
        # absolute paths, email, machine details - to stdout, in a repo whose
        # Global Constraints forbid a real transcript ever being captured. It
        # would also make these assertions depend on that transcript's contents.
        $extra = $ExtraArgs -join ' '
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$BridgeHome'
            `$env:CLAUDE_CODE_SESSION_ID = 'no-such-session'
            & '$script:Script' -ClaudeHome '$BridgeHome' $extra
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
    }

    # $json is deliberately UNTYPED. A `[string]` parameter coerces `$null` to
    # `''`, so `if ($null -ne $json)` would always be true and the "config file
    # is absent" case below would silently become "config file is empty" - a
    # different branch of the script, and not the one the spec names.
    function New-Home($json) {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        if ($null -ne $json) { Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value $json }
        return $h
    }
}

Describe 'enabled gate' {
    It 'exits 1 when enabled is false' {
        $h = New-Home '{"enabled": false}'
        (Invoke-Bridge -BridgeHome $h).Code | Should -Be 1
    }
    It 'exits 1 when the config file is absent' {
        $h = New-Home $null
        (Join-Path $h 'advisor-bridge.json') | Should -Not -Exist   # the case really is "absent"
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'advisor-bridge'
    }
    It 'exits 1 when the config file is not valid JSON' {
        $h = New-Home '{ this is not json'
        (Invoke-Bridge -BridgeHome $h).Code | Should -Be 1
    }
    It 'writes no log row on an exit-1 path' {
        $h = New-Home '{"enabled": false}'
        Invoke-Bridge -BridgeHome $h | Out-Null
        Join-Path $h 'advisor-bridge.log.jsonl' | Should -Not -Exist
    }
}

Describe 'numeric coercion' {
    It 'falls back to the default on a non-numeric charBudget' {
        $h = New-Home '{"enabled": true, "charBudget": "wide"}'
        $r = Invoke-Bridge -BridgeHome $h -ExtraArgs @('-DryRun')
        $r.Text | Should -Not -Match 'wide'
        # No exit-code assertion. At this task the script ends after the config
        # read, so it exits 0; asserting non-zero would fail here and start
        # passing only as a side effect of later tasks appending code. The
        # negative below is the real check - `-as [int]` must not throw under
        # $ErrorActionPreference = 'Stop'.
        $r.Text | Should -Not -Match 'Cannot convert'
    }
    It 'falls back to the default on a negative timeoutSec' {
        $h = New-Home '{"enabled": true, "timeoutSec": -5}'
        $r = Invoke-Bridge -BridgeHome $h -ExtraArgs @('-DryRun')
        $r.Text | Should -Not -Match 'Cannot convert'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Config.Tests.ps1 -Output Detailed`
Expected: FAIL — `advisor-bridge.ps1` does not exist.

- [ ] **Step 3: Write the config seed**

`advisor-bridge/advisor-bridge.example.json`:

```json
{
  "enabled": false,
  "model": "claude-fable-5-1",
  "charBudget": 80000,
  "maxToolResultChars": 2000,
  "timeoutSec": 240
}
```

`enabled` seeds **false**. A package that spends $0.20–0.40 per call must not be live before the user has opted in once, matching `ollama-workers.example.json`.

- [ ] **Step 4: Write the script's head**

`advisor-bridge/scripts/advisor-bridge.ps1`:

```powershell
#Requires -Version 7
<#
.SYNOPSIS
Sends this Claude Code session's own transcript to an Anthropic model for
advice, from a session whose backend is not Anthropic.

.DESCRIPTION
`ollama launch claude` exports ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN and all
three ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL vars, so one process serves
exactly one model from one endpoint. The built-in advisor tool is disabled there
by the model catalog, and registering it would only get GLM advising GLM. So the
advisor is a child process with a scrubbed environment, pointed back at
Anthropic.

Exit codes: 0 advice returned, 1 the wrapper refused before spawning,
2 the call was attempted and its result is not trustworthy.
#>
[CmdletBinding()]
param(
    [string]$ClaudeHome,
    [string]$EnvelopeFile,
    [int]$TimeoutSec,
    [switch]$DryRun,
    # Test seam, honoured only alongside -DryRun (see Task 6). The spec names
    # four seams; this is a fifth the plan adds, because the spec's own Testing
    # section asks for the pre-spawn guard to be covered and no external input
    # can otherwise make that guard trip.
    [string]$InjectEnvKey
)

$ErrorActionPreference = 'Stop'

# -ClaudeHome exists so the test suite can point config, log, persona and
# scratch somewhere disposable. Without it every config and guard test would
# read the developer's live config - which seeds enabled:false, so each would
# exit 1 at the gate before reaching the behaviour under test - and would append
# rows to the real log the cost calibration reads. A child pwsh with USERPROFILE
# overridden is not a substitute: $HOME and ~ resolve once in PowerShell and do
# not follow a mid-process change.
$claudeHome = if ($ClaudeHome) { $ClaudeHome } else { Join-Path $HOME '.claude' }
$configPath  = Join-Path $claudeHome 'advisor-bridge.json'
$personaPath = Join-Path $claudeHome 'advisor-bridge-persona.md'
$logPath     = Join-Path $claudeHome 'advisor-bridge.log.jsonl'
$scratchDir  = Join-Path $claudeHome 'advisor-bridge-scratch'

function Fail([string]$message, [int]$code = 1) {
    [Console]::Error.WriteLine("advisor-bridge: $message")
    exit $code
}

# -as, not a cast: a non-numeric or negative value in user-editable JSON would
# otherwise throw a terminating error under $ErrorActionPreference = 'Stop' and
# take the wrapper down before it could say what was wrong with the config.
# Same reasoning as ollama-worker.ps1:128-133.
function Get-PositiveInt($value, [int]$default) {
    $n = $value -as [int]
    if ($null -eq $n -or $n -le 0) { return $default }
    return $n
}

# --- 1. Read config --------------------------------------------------------
$cfgRaw = $null
if (Test-Path -LiteralPath $configPath) {
    try { $cfgRaw = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json }
    catch { $cfgRaw = $null }
}

# --- 2. Enabled gate -------------------------------------------------------
# Enforced by the mechanism, not by the skill's prose. A caller can invoke this
# on stale context after a compact; every other constraint here is checked
# rather than trusted for the same reason. A missing or unreadable file counts
# as disabled, so a broken config fails closed rather than spending money.
if ($null -eq $cfgRaw -or $cfgRaw.enabled -ne $true) {
    Fail "disabled or unreadable config: $configPath`n  Enable with: /advisor-bridge on"
}

$model              = if ($cfgRaw.model) { $cfgRaw.model } else { 'claude-fable-5-1' }
$charBudget         = Get-PositiveInt $cfgRaw.charBudget 80000
$maxToolResultChars = Get-PositiveInt $cfgRaw.maxToolResultChars 2000
$timeoutSeconds     = if ($PSBoundParameters.ContainsKey('TimeoutSec')) {
                          Get-PositiveInt $TimeoutSec 240
                      } else {
                          Get-PositiveInt $cfgRaw.timeoutSec 240
                      }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Config.Tests.ps1 -Output Detailed`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/advisor-bridge.example.json advisor-bridge/tests/Config.Tests.ps1
git commit -m "feat(advisor-bridge): config read and the enabled gate"
```

---

### Task 3: Session locator

**Files:**
- Modify: `advisor-bridge/scripts/advisor-bridge.ps1` (append)
- Test: `advisor-bridge/tests/Locator.Tests.ps1`

**Interfaces:**
- Consumes: `Fail`, `$claudeHome` from Task 2.
- Produces: `$transcriptPath` — the single resolved `.jsonl` path.

Note the asymmetry the spec calls out: the locator's base is the **caller's** `CLAUDE_CONFIG_DIR` (or `~/.claude`), which is an environment variable a test can set — *not* `-ClaudeHome`, which redirects this script's own files. The child is always launched against the real `~/.claude`, because that is where the Anthropic credential lives.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Locator.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Script = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'

    function New-Enabled-Home {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        return $h
    }
    function New-Projects([string]$base, [string[]]$projectDirs, [string]$sessionId) {
        foreach ($d in $projectDirs) {
            $p = Join-Path $base 'projects' $d
            New-Item -ItemType Directory -Path $p -Force | Out-Null
            Set-Content -LiteralPath (Join-Path $p "$sessionId.jsonl") -Value '{"type":"user"}'
        }
    }
    # -BridgeHome, not -Home: `$HOME` is ReadOnly + AllScope, so a parameter of
    # that name cannot be bound and every call would throw at parameter binding.
    # Same reason as Config.Tests.ps1.
    function Invoke-Locate {
        param([string]$BridgeHome, [string]$ConfigDir, [string]$SessionId)
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$ConfigDir'
            `$env:CLAUDE_CODE_SESSION_ID = '$SessionId'
            & '$script:Script' -ClaudeHome '$BridgeHome' -DryRun
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
    }
}

Describe 'session locator' {
    It 'exits 1 with a remedy when the session id is unset' {
        $h = New-Enabled-Home
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId ''
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'CLAUDE_CODE_SESSION_ID'
    }
    It 'exits 1 naming the search path when nothing matches' {
        $h = New-Enabled-Home
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'no-such-session'
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'no transcript'
    }
    It 'exits 1 listing every path when more than one matches' {
        $h = New-Enabled-Home
        New-Projects -base $h -projectDirs @('C--a--repo', 'C--b--repo') -sessionId 'dup-id'
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'dup-id'
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'matches 2 transcripts'
        $r.Text | Should -Match 'C--a--repo'
        $r.Text | Should -Match 'C--b--repo'
    }
    It 'never falls back to a nearby transcript' {
        $h = New-Enabled-Home
        New-Projects -base $h -projectDirs @('C--a--repo') -sessionId 'some-other-session'
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'wanted-session'
        $r.Code | Should -Be 1
        $r.Text | Should -Not -Match 'some-other-session'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Locator.Tests.ps1 -Output Detailed`
Expected: FAIL — nothing looks for a transcript yet.

- [ ] **Step 3: Append the locator**

```powershell
# --- 5. Locate the caller's transcript -------------------------------------
# Glob, rather than recomputing Claude Code's cwd-to-directory-name mangling
# (C:\Users\<user>\... -> C--Users-<user>-...). That rule is undocumented, and
# reimplementing it buys nothing a glob does not already give while its failure
# mode is a wrong-or-missing file rather than an error.
#
# The base is the CALLER's config dir, not $claudeHome: -ClaudeHome redirects
# this script's own files, whereas the transcript belongs to whichever session
# invoked us. Ollama sessions do not set CLAUDE_CONFIG_DIR today, but honouring
# it costs one line and ignoring it would be a wrong-file failure, not an error.
$sessionId = $env:CLAUDE_CODE_SESSION_ID
if (-not $sessionId) {
    Fail "CLAUDE_CODE_SESSION_ID is not set - this script must run inside a Claude Code session, not from a bare shell."
}

$callerBase = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$pattern    = Join-Path $callerBase 'projects' '*' "$sessionId.jsonl"
$found      = @(Get-Item -Path $pattern -ErrorAction SilentlyContinue)

if ($found.Count -eq 0) {
    Fail "no transcript for session $sessionId under $(Join-Path $callerBase 'projects')\*\`n  The session may not have been written yet; send one message and retry."
}
if ($found.Count -gt 1) {
    $list = ($found | ForEach-Object { "    $($_.FullName)" }) -join "`n"
    Fail "session id $sessionId matches $($found.Count) transcripts:`n$list`n  Rendering the wrong one would advise on someone else's session. Delete or move`n  the stale copy, or set CLAUDE_CONFIG_DIR to disambiguate."
}
$transcriptPath = $found[0].FullName
```

There is deliberately no tiebreak. "Newest nearby" is exactly the heuristic this refuses: advising on the wrong session is worse than not advising.

- [ ] **Step 4: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Locator.Tests.ps1 -Output Detailed`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/tests/Locator.Tests.ps1
git commit -m "feat(advisor-bridge): session locator with three fail-closed paths"
```

---

### Task 4: Renderer — parsing, filters, block caps, header

**Files:**
- Modify: `advisor-bridge/scripts/advisor-bridge.ps1` (append)
- Test: `advisor-bridge/tests/Render.Tests.ps1`

**Interfaces:**
- Consumes: `$transcriptPath`, `$maxToolResultChars`, `Fail`.
- Produces: `Read-Turns([string]$path)` returning `@{ Turns = <object[]>; Skipped = <int> }` where each turn is `@{ Role = 'user'|'assistant'; Text = <string> }`; `Format-Block($block, [int]$maxToolResult)` returning the rendered string for one content block; a **minimal `-DryRun` emitter** (Step 5) printing `render`, `chars_sent`, `turns_rendered`, `turns_elided`, `lines_skipped`.

**`-DryRun` is built in this task, not in Task 6.** Every assertion in
`Render.Tests.ps1` reads its JSON, so a renderer whose seam arrives two tasks later
is a renderer whose tests cannot pass in its own task. Task 5 extends the emitter
with the budgeted values and Task 6 relocates it below the environment build and
adds `env`/`args`/`cwd`/`exe`.

**If `SCHEMA.md` from Task 1 records a nesting other than `message.content[]`, adjust the property paths below to match it.** The schema on disk is the authority.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Render.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'
    # Dot-source just the pure functions by running the script with a switch that
    # stops before any I/O. -DryRun prints JSON including the render, which is
    # what these assert against.
    function Render-Fixture {
        param([string]$Fixture, [hashtable]$Config = @{})
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        $cfg = @{ enabled = $true } + $Config
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value ($cfg | ConvertTo-Json)
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures $Fixture) (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun" 2>&1
        ($out -join "`n") | ConvertFrom-Json
    }
}

Describe 'record filters' {
    It 'drops attachment records and keeps user and assistant' {
        $r = Render-Fixture 'basic.jsonl'
        $r.render | Should -Not -Match 'ATTACHMENT-MARKER'
        $r.turns_rendered | Should -Be 2
    }
    It 'drops isSidechain true, keeps false, and keeps the key absent entirely' {
        $r = Render-Fixture 'sidechain.jsonl'
        $r.turns_rendered | Should -Be 2
        $r.render | Should -Not -Match 'SIDECHAIN-MARKER'
    }
    It 'reports unparseable lines rather than dying on them' {
        $r = Render-Fixture 'truncated.jsonl'
        $r.lines_skipped | Should -Be 1
        $r.turns_rendered | Should -Be 2
    }
}
```

**Every filter below uses `-match` with an escaped regex, never `-like`.** In
PowerShell's wildcard grammar `[...]` is a *character class*, so `-like '*[tool_use]*'`
means "contains any one of `t o l _ u s e`" — it matches essentially every line in the
render, including the 2000-char `tool_result` line, and `Measure-Object -Maximum` then
returns the whole render's longest line. Both cap tests would fail on correct output
while appearing to test the cap.

Each cap test also asserts its marker is **present** before measuring: an empty
pipeline gives `(@() | Measure-Object -Maximum).Maximum` = `$null`, and `$null` compares
`-le` any number, so a mis-synthesized fixture or an over-eager filter would make the
assertion pass while measuring nothing at all.

```powershell
Describe 'block caps' {
    It 'caps thinking at 600 chars' {
        $r = Render-Fixture 'caps.jsonl'
        $r.render | Should -Match '\[thinking\]'
        ([regex]::Matches($r.render, '\[thinking\] (.{0,700}?)(\r?\n|$)') |
            ForEach-Object { $_.Groups[1].Value.Length } |
            Measure-Object -Maximum).Maximum | Should -BeLessOrEqual 620
        # 620, not 600: Limit-Text appends ' [truncated]' (12 chars) AFTER the
        # 600-char cut, so a capped line is 612 long. Asserting 600 fails on
        # correct output.
    }
    It 'caps tool_use input at 800 chars' {
        $r = Render-Fixture 'caps.jsonl'
        $r.render | Should -Match '\[tool_use\]'
        ($r.render -split "`n" | Where-Object { $_ -match '\[tool_use\]' } |
            ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum |
            Should -BeLessOrEqual 900   # 800 + the name prefix
    }
    It 'caps tool_result mid-transcript AND inside the last 12 turns' {
        $r = Render-Fixture 'caps.jsonl' -Config @{ maxToolResultChars = 100 }
        $r.render | Should -Match '\[tool_result\]'
        ($r.render -split "`n" | Where-Object { $_ -match '\[tool_result\]' } |
            ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum |
            Should -BeLessOrEqual 130
    }
}

Describe 'header' {
    It 'names cwd, branch, model, turn count, elided and skipped' {
        $r = Render-Fixture 'basic.jsonl'
        $r.render | Should -Match 'cwd:'
        $r.render | Should -Match 'branch:'
        $r.render | Should -Match 'caller model:'
        $r.render | Should -Match 'turns:'
        $r.render | Should -Match 'elided:'
        $r.render | Should -Match 'unparseable lines skipped:'
    }
}

Describe 'non-empty check' {
    It 'exits 1 naming the transcript when nothing survives the filters' {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'empty.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 1
        ($out -join "`n") | Should -Match 'fix-session.jsonl'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Render.Tests.ps1 -Output Detailed`
Expected: FAIL — no renderer, and `-DryRun` prints nothing yet.

- [ ] **Step 3: Append the renderer**

```powershell
# --- 6. Render -------------------------------------------------------------
# Line by line, each line in its own try/catch, and the file opened share-read.
# Both halves are load-bearing: the caller's own Claude Code process is
# appending to this file while we read it, so the last line is routinely a
# partial record. Under $ErrorActionPreference = 'Stop' an unguarded
# ConvertFrom-Json on it is a terminating error that would kill the wrapper
# before any log row and with an exit code outside the published table. Same
# hazard, same remedy, as ollama-worker.ps1:412-424.
function Read-Turns([string]$path) {
    $turns   = [System.Collections.Generic.List[object]]::new()
    $skipped = 0
    $fs      = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    $reader  = [System.IO.StreamReader]::new($fs)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $line.Trim()) { continue }
            $rec = $null
            try { $rec = $line | ConvertFrom-Json } catch { $skipped++; continue }

            if ($rec.type -notin 'user', 'assistant') { continue }
            # -ne $true, NOT -eq $false: a record omitting the field entirely is
            # a main-agent record and must be kept.
            if ($rec.isSidechain -eq $true) { continue }

            $turns.Add($rec)
        }
    }
    finally { $reader.Dispose(); $fs.Dispose() }
    return @{ Turns = $turns; Skipped = $skipped }
}

function Limit-Text([string]$s, [int]$max) {
    if ($null -eq $s) { return '' }
    if ($s.Length -le $max) { return $s }
    return $s.Substring(0, $max) + ' [truncated]'
}

function Format-Block($block, [int]$maxToolResult) {
    switch ($block.type) {
        'text'        { return $block.text }
        'thinking'    { return "[thinking] " + (Limit-Text $block.thinking 600) }
        'tool_use'    {
            $input = $block.input | ConvertTo-Json -Depth 6 -Compress
            return "[tool_use] $($block.name) " + (Limit-Text $input 800)
        }
        'tool_result' {
            $content = if ($block.content -is [string]) { $block.content }
                       else { $block.content | ConvertTo-Json -Depth 6 -Compress }
            return "[tool_result] " + (Limit-Text $content $maxToolResult)
        }
        default       { return '' }
    }
}

function Format-Turn($rec, [int]$maxToolResult) {
    # message.content is EITHER a block array OR a bare string - Claude Code
    # writes plain-string content for ordinary typed user messages, which is
    # exactly the shape of the first user message the whole budget sequence
    # exists to preserve. Wrapping a string in @() yields a one-element array
    # whose element has no .type, so Format-Block's switch would fall to
    # `default` and return '' - the turn would render as a header with an empty
    # body, silently. (Task 1 Step 5's measurement code branches on this same
    # distinction, which is where the shape is confirmed to exist.)
    if ($rec.message.content -is [string]) {
        return "--- $($rec.type) ---`n$($rec.message.content)"
    }
    $blocks = @($rec.message.content)
    $parts  = foreach ($b in $blocks) { Format-Block $b $maxToolResult }
    $body   = ($parts | Where-Object { $_ }) -join "`n"
    return "--- $($rec.type) ---`n$body"
}

$read     = Read-Turns $transcriptPath
$allTurns = @($read.Turns)
$skipped  = $read.Skipped

# --- 7. Non-empty check ----------------------------------------------------
# A full-price call over an empty render returns confident advice about nothing.
if ($allTurns.Count -eq 0) {
    Fail "no user or assistant turns survived the filters in $transcriptPath`n  Nothing to advise on."
}
```

- [ ] **Step 4: Append the header builder**

```powershell
function New-Header([int]$total, [int]$elided, [int]$skippedLines) {
    $branch = try { (& git rev-parse --abbrev-ref HEAD 2>$null) } catch { $null }
    if (-not $branch) { $branch = '(not a git repo)' }
    @(
        "cwd: $((Get-Location).Path)"
        "branch: $branch"
        "caller model: $($env:ANTHROPIC_DEFAULT_OPUS_MODEL ?? '(unknown)')"
        "turns: $total"
        "elided: $elided"
        "unparseable lines skipped: $skippedLines"
        ''
    ) -join "`n"
}
```

- [ ] **Step 5: Append the minimal `-DryRun` emitter**

```powershell
# --- -DryRun (minimal) -----------------------------------------------------
# A deliverable seam, not a test-only afterthought, and it belongs in THIS task:
# every assertion in Render.Tests.ps1 reads this JSON. It is deliberately
# outside the stdout/exit contract - it prints JSON and exits 0 without
# spawning, which is not "advice returned" in the sense of the exit table.
#
# Task 5 replaces the body with the budgeted values; Task 6 moves the block
# below the environment build and adds env, args, cwd and exe. Until then it
# reports only what the renderer itself knows, with turns_elided fixed at 0
# because nothing elides yet.
if ($DryRun) {
    $rendered = (New-Header $allTurns.Count 0 $skipped) +
                (($allTurns | ForEach-Object { Format-Turn $_ $maxToolResultChars }) -join "`n`n")
    [ordered]@{
        render         = $rendered
        chars_sent     = $rendered.Length
        turns_rendered = $allTurns.Count
        turns_elided   = 0
        lines_skipped  = $skipped
    } | ConvertTo-Json -Depth 8
    exit 0
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Render.Tests.ps1 -Output Detailed`
Expected: the filter, cap, header and non-empty tests PASS. The budget tests do not exist yet.

- [ ] **Step 7: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/tests/Render.Tests.ps1
git commit -m "feat(advisor-bridge): transcript renderer with per-line parse tolerance"
```

---

### Task 5: Renderer — budget enforcement

The six-step sequence, which must provably terminate from either end.

**Files:**
- Modify: `advisor-bridge/scripts/advisor-bridge.ps1` (append)
- Modify: `advisor-bridge/tests/Render.Tests.ps1` (append a `Describe`)

**Interfaces:**
- Consumes: `$allTurns`, `$charBudget`, `$maxToolResultChars`, `Format-Turn`, `Limit-Text`, `New-Header`.
- Produces: `$rendered` (the final string, guaranteed `<= $charBudget`), `$elided` (int), `$turnsRendered` (int).

- [ ] **Step 1: Write the failing tests**

Append to `advisor-bridge/tests/Render.Tests.ps1`:

```powershell
Describe 'budget enforcement' {
    It 'renders the first user message in full even when it falls outside the last 12 turns' {
        $r = Render-Fixture 'long.jsonl' -Config @{ charBudget = 12000 }
        $r.render | Should -Match 'FIRST-MESSAGE-MARKER-END'
    }
    It 'marks elided middle turns with a count' {
        $r = Render-Fixture 'long.jsonl' -Config @{ charBudget = 12000 }
        $r.render | Should -Match '\[\d+ turns elided\]'
        $r.turns_elided | Should -BeGreaterThan 0
    }
    It 'terminates under budget when the first message plus twelve turns alone exceed it' {
        $r = Render-Fixture 'long.jsonl' -Config @{ charBudget = 3000 }
        $r.chars_sent | Should -BeLessOrEqual 3000
        $r.render | Should -Match '\[truncated\]'
    }
    It 'terminates under budget when a single most-recent turn exceeds it' {
        $r = Render-Fixture 'oversized-tail.jsonl' -Config @{ charBudget = 5000 }
        $r.chars_sent | Should -BeLessOrEqual 5000
        $r.render | Should -Match '\[truncated\]'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Render.Tests.ps1 -Output Detailed`
Expected: the four new tests FAIL — nothing enforces a budget yet.

- [ ] **Step 3: Append budget enforcement**

```powershell
# --- Budget ----------------------------------------------------------------
# Six steps. 1 and 2 are preservation floors, not reductions; only 3 through 6
# remove text, and they run in ascending order of what it costs to lose the
# content - which is why the first user message is cut LAST rather than first.
#
# Steps 4-6 truncate `text` blocks, the one block type Format-Block renders in
# full and therefore the only content no cap otherwise bounds. Without all three
# the sequence has no terminal step: a first message plus twelve turns that
# together exceed the budget, or a single oversized final turn, would ship over
# budget at full per-call cost and say nothing about it.
$TAIL = 12

$firstUserIdx = 0
for ($i = 0; $i -lt $allTurns.Count; $i++) {
    if ($allTurns[$i].type -eq 'user') { $firstUserIdx = $i; break }
}

function Join-Render([string[]]$bodies, [int]$elidedCount, [int]$total, [int]$skippedLines) {
    (New-Header $total $elidedCount $skippedLines) + ($bodies -join "`n`n")
}

# Step 1 + 2: the floors.
$tailStart = [Math]::Max($firstUserIdx + 1, $allTurns.Count - $TAIL)
$keepIdx   = [System.Collections.Generic.List[int]]::new()
$keepIdx.Add($firstUserIdx)
for ($i = $tailStart; $i -lt $allTurns.Count; $i++) { $keepIdx.Add($i) }

# Step 3: drop middle turns oldest-first. Everything between the first user
# message and the tail window is already excluded above; the elision marker is
# what tells the advisor it is not reading everything.
$elided = $allTurns.Count - $keepIdx.Count

function Build([hashtable]$truncate) {
    $bodies = [System.Collections.Generic.List[string]]::new()
    for ($k = 0; $k -lt $keepIdx.Count; $k++) {
        $idx  = $keepIdx[$k]
        $body = Format-Turn $allTurns[$idx] $maxToolResultChars
        if ($truncate.ContainsKey($idx)) { $body = Limit-Text $body $truncate[$idx] }
        if ($k -eq 1 -and $elided -gt 0) { $bodies.Add("[$elided turns elided]") }
        $bodies.Add($body)
    }
    return Join-Render $bodies.ToArray() $elided $allTurns.Count $skipped
}

$truncate = @{}
$rendered = Build $truncate

# Step 4: truncate the tail window oldest-first, down to the first user message
# plus the most recent turn.
$tailIdx = @($keepIdx | Where-Object { $_ -ne $firstUserIdx })
for ($t = 0; $t -lt $tailIdx.Count - 1 -and $rendered.Length -gt $charBudget; $t++) {
    $truncate[$tailIdx[$t]] = 200
    $rendered = Build $truncate
}

# Step 5: truncate the most recent turn itself.
if ($rendered.Length -gt $charBudget -and $tailIdx.Count -gt 0) {
    $last = $tailIdx[-1]
    $over = $rendered.Length - $charBudget
    $cur  = (Format-Turn $allTurns[$last] $maxToolResultChars).Length
    $truncate[$last] = [Math]::Max(200, $cur - $over - 64)
    $rendered = Build $truncate
}

# Step 6: last resort - truncate the first user message.
if ($rendered.Length -gt $charBudget) {
    $over = $rendered.Length - $charBudget
    $cur  = (Format-Turn $allTurns[$firstUserIdx] $maxToolResultChars).Length
    $truncate[$firstUserIdx] = [Math]::Max(200, $cur - $over - 64)
    $rendered = Build $truncate
}

# The floor of 200 chars per turn means an absurdly small charBudget cannot be
# met. That is a config error, not a case to support - but it must not ship a
# silent overrun either.
if ($rendered.Length -gt $charBudget) {
    Fail "charBudget $charBudget is too small to render even a minimal transcript ($($rendered.Length) chars)`n  Raise charBudget in $configPath."
}

$turnsRendered = $keepIdx.Count
$charsSent     = $rendered.Length
```

- [ ] **Step 4: Update the `-DryRun` emitter to report the budgeted render**

Replace the minimal emitter Task 4 appended with this one. It must sit **below** the
budget block just added — `$rendered`, `$charsSent`, `$turnsRendered` and `$elided`
do not exist above it, and leaving the old emitter in place would report the
unbudgeted render, so the two termination tests would read a `chars_sent` no budget
step ever touched and pass on a script that never terminates.

```powershell
if ($DryRun) {
    [ordered]@{
        render         = $rendered
        chars_sent     = $charsSent
        turns_rendered = $turnsRendered
        turns_elided   = $elided
        lines_skipped  = $skipped
    } | ConvertTo-Json -Depth 8
    exit 0
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Render.Tests.ps1 -Output Detailed`
Expected: PASS, all render tests.

- [ ] **Step 6: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/tests/Render.Tests.ps1
git commit -m "feat(advisor-bridge): budget enforcement that terminates from either end"
```

---

### Task 6: Child environment, pre-spawn guard, and `-DryRun`

**Files:**
- Modify: `advisor-bridge/scripts/advisor-bridge.ps1` (append)
- Test: `advisor-bridge/tests/Env.Tests.ps1`

**Interfaces:**
- Consumes: `$rendered`, `$model`, `$personaPath`, `$scratchDir`, `Fail`.
- Produces: `$psi` — a fully configured `ProcessStartInfo`; `$ENV_WHITELIST` — the exact key set the guard compares against; `-DryRun` output as a JSON object with keys `env`, `args`, `cwd`, `render`, `chars_sent`, `turns_rendered`, `turns_elided`, `lines_skipped`.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Env.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'

    function Invoke-DryRun {
        param([hashtable]$PoisonEnv = @{}, [int]$PersonaChars = 20, [string[]]$Extra = @())
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value ('x' * $PersonaChars)
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $sets = ($PoisonEnv.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = '$($_.Value)'" }) -join "`n"
        $out = & pwsh -NoProfile -Command "
            $sets
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun $($Extra -join ' ')
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n"); Home = $h }
    }
}

Describe 'environment scrub' {
    It 'leaves the child environment equal to the whitelist exactly' {
        $r = Invoke-DryRun -PoisonEnv @{
            ANTHROPIC_BASE_URL           = 'http://127.0.0.1:11434'
            ANTHROPIC_AUTH_TOKEN         = 'ollama'
            ANTHROPIC_DEFAULT_OPUS_MODEL = 'glm-5.3-flash:cloud'
            CLAUDE_CODE_SUBAGENT_MODEL   = 'glm-5.3-flash:cloud'
        }
        $r.Code | Should -Be 0
        $keys = ($r.Text | ConvertFrom-Json).env.PSObject.Properties.Name | Sort-Object
        $expected = @('APPDATA','CLAUDE_EFFORT','COMSPEC','HOME','LOCALAPPDATA',
                      'PATH','PATHEXT','SystemRoot','TEMP','USERPROFILE') | Sort-Object
        ($keys -join ',') | Should -Be ($expected -join ',')
    }
    It 'lets no CLAUDE_CODE_SUBAGENT_MODEL through, which a prefix check would miss' {
        $r = Invoke-DryRun -PoisonEnv @{ CLAUDE_CODE_SUBAGENT_MODEL = 'glm-5.3-flash:cloud' }
        ($r.Text | ConvertFrom-Json).env.PSObject.Properties.Name |
            Should -Not -Contain 'CLAUDE_CODE_SUBAGENT_MODEL'
    }
    It 'sets CLAUDE_EFFORT explicitly rather than inheriting it' {
        $r = Invoke-DryRun -PoisonEnv @{ CLAUDE_EFFORT = 'low' }
        ($r.Text | ConvertFrom-Json).env.CLAUDE_EFFORT | Should -Be 'xhigh'
    }
}

Describe 'pre-spawn guard' {
    # The negative test the guard would otherwise lack. Without it, deleting the
    # whole comparison block leaves every test in this file green - the other
    # three only prove the CONSTRUCTION is right, and construction and guard are
    # derived from the same whitelist, so no poisoned input can separate them.
    It 'exits 2 and logs model_guard when the child environment gains a stray key' {
        $r = Invoke-DryRun -Extra @('-InjectEnvKey', 'STRAY_KEY')
        $r.Code | Should -Be 2
        $r.Text | Should -Match 'does not match the whitelist'
        $r.Text | Should -Match 'STRAY_KEY'
        $row = (Get-Content (Join-Path $r.Home 'advisor-bridge.log.jsonl') |
                Select-Object -Last 1) | ConvertFrom-Json
        $row.verdict | Should -Be 'model_guard'
    }
    It 'refuses the injection seam outside a dry run' {
        # Exit 1, and no log row: the seam is rejected before anything is
        # attempted, so it is a wrapper refusal, not an untrustworthy result.
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -InjectEnvKey 'STRAY_KEY'
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 1
        ($out -join "`n") | Should -Match 'requires -DryRun'
    }
}

Describe 'persona preflight' {
    It 'exits 1 naming the limit when the persona is too large' {
        $r = Invoke-DryRun -PersonaChars 20000
        $r.Code | Should -Be 1
        $r.Text | Should -Match '16000'
    }
}

Describe 'argument list' {
    It 'passes the persona as one argument and never builds a command string' {
        $r = Invoke-DryRun
        $a = ($r.Text | ConvertFrom-Json).args
        $a | Should -Contain '--system-prompt'
        $a | Should -Contain '-p'
        $a | Should -Contain '--strict-mcp-config'
        ($a -join ' ') | Should -Match '--setting-sources'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Env.Tests.ps1 -Output Detailed`
Expected: FAIL — nothing builds an environment yet.

- [ ] **Step 3: Append executable resolution and the persona preflight**

Place these BEFORE the locator in the final file, matching the spec's order of operations (steps 3 and 4 run before step 5). Move the block if the append lands it later.

```powershell
# --- 3. Resolve the claude executable --------------------------------------
# A missing binary must be a named preflight blocker with a remedy, not a raw
# spawn exception with no exit-table entry. Same shape as ollama-worker.ps1's
# Get-OllamaPath.
function Get-ClaudePath {
    $p = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if (-not $p) { $p = Join-Path $HOME '.local\bin\claude.exe' }
    if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    return $null
}
$claudeExe = Get-ClaudePath
if (-not $claudeExe) {
    Fail "claude executable not found on PATH or at ~/.local/bin/claude.exe`n  Install the Claude Code CLI, then retry."
}

# --- 4. Read the persona ---------------------------------------------------
if (-not (Test-Path -LiteralPath $personaPath)) {
    Fail "persona not found: $personaPath`n  Re-run advisor-bridge/install.ps1 to place it."
}
$persona = try { Get-Content -Raw -LiteralPath $personaPath } catch { $null }
if ($null -eq $persona -or -not $persona.Trim()) {
    Fail "persona is empty or unreadable: $personaPath"
}
# Windows caps a command line at 32767 chars and the persona is the only
# unbounded element on it. A persona this long is a bug in the persona.
if ($persona.Length -gt 16000) {
    Fail "persona is $($persona.Length) chars, over the 16000 limit: $personaPath"
}
```

- [ ] **Step 4: Append environment construction and the pre-spawn guard**

```powershell
# --- 8. Build the child environment from empty -----------------------------
# A whitelist, not a blacklist of ANTHROPIC_* vars to unset. A blacklist is one
# Ollama release away from missing a newly-exported variable, and the symptom of
# that miss is GLM answering in the advisor's voice - which reads as success.
#
# PATHEXT and COMSPEC are on the list because `claude` on Windows is commonly a
# .cmd shim, and a shim launched with UseShellExecute = $false needs both.
$ENV_WHITELIST = @('PATH','PATHEXT','COMSPEC','USERPROFILE','HOME','TEMP',
                   'SystemRoot','APPDATA','LOCALAPPDATA','CLAUDE_EFFORT')

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName               = $claudeExe
$psi.UseShellExecute        = $false
$psi.RedirectStandardInput  = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.WorkingDirectory       = $scratchDir

# .Environment is PRE-POPULATED from the current process. "From empty" requires
# this explicit Clear() - it is not the default, and forgetting it is exactly
# the mistake the guard below exists to catch.
$psi.Environment.Clear()
foreach ($k in $ENV_WHITELIST) {
    if ($k -in 'CLAUDE_EFFORT', 'HOME') { continue }
    $v = [Environment]::GetEnvironmentVariable($k)
    if ($null -ne $v) { $psi.Environment[$k] = $v }
}
# Set explicitly, not inherited, so its value is a decision recorded here rather
# than an accident of what the parent happened to export.
$psi.Environment['CLAUDE_EFFORT'] = 'xhigh'

# HOME is NOT a Windows environment variable - it is a git-bash export. Plain
# pwsh does not have $env:HOME (PowerShell's $HOME automatic variable is derived
# from USERPROFILE and is a different thing). Inheriting it would make both the
# child's environment and the guard's key set depend on which shell launched the
# wrapper, so it is derived here instead and the child always gets one.
$homeDir = [Environment]::GetEnvironmentVariable('HOME')
if (-not $homeDir) { $homeDir = [Environment]::GetEnvironmentVariable('USERPROFILE') }
if ($homeDir) { $psi.Environment['HOME'] = $homeDir }

foreach ($a in @(
    '-p'
    '--model', $model
    '--system-prompt', $persona
    '--tools', ''
    '--strict-mcp-config'
    '--setting-sources', ''
    '--output-format', 'json'
)) { [void]$psi.ArgumentList.Add($a) }
```

`ArgumentList`, never a hand-built `Arguments` string: it applies the CRT's quoting rules per element. The persona is arbitrary user-editable markdown with quotes, backslashes and newlines, and editing it is this project's documented iteration loop — `ollama-worker.ps1:344-367` records what the naive version did to two *allowlisted* short strings.

```powershell
# --- Test seam: force a guard mismatch -------------------------------------
# Honoured ONLY under -DryRun, which spawns nothing and bills nothing, so it
# cannot alter a real call. It exists because the guard below is otherwise
# unreachable by any external input: $actual and $expected are derived from the
# same whitelist and the same GetEnvironmentVariable calls, so nothing a test
# can set makes them diverge - and a guard that no test can trip is a guard that
# could be deleted with every test still green.
if ($InjectEnvKey) {
    if (-not $DryRun) { Fail "-InjectEnvKey is a test seam and requires -DryRun" }
    $psi.Environment[$InjectEnvKey] = 'injected'
}

# --- Log row writer --------------------------------------------------------
# Defined here, above the guard, rather than beside the spawn: the pre-spawn
# guard is itself an exit-2 path, and both the Global Constraints and the spec
# require a row on every exit-0 and exit-2 path - "the pre-spawn guard at step 9
# included, since the exit-2 table gives it a verdict and a verdict only exists
# inside a row".
#
# $haveUsage keys on ENVELOPE PRESENCE, not on the verdict. Keying it on
# `$verdict -eq 'ok'` would null the token and cost fields on model_guard and on
# an envelope-bearing child_error - calls that were really billed - so the cost
# column `## Cost` calibrates from would under-report real spend on exactly the
# guard-trip path. The nulls exist to distinguish a call that produced no
# envelope from a free one; that is a question about the envelope, not the
# verdict.
function Write-LogRow([string]$verdict, $envelope, [int]$durationMs, [string]$source) {
    $haveUsage = [bool]$envelope -and
                 ($envelope.PSObject.Properties.Name -contains 'modelUsage') -and
                 $envelope.modelUsage
    $row = [ordered]@{
        ts             = (Get-Date).ToUniversalTime().ToString('o')
        session_id     = $sessionId
        model          = $model
        chars_sent     = $charsSent
        turns_rendered = $turnsRendered
        turns_elided   = $elided
        lines_skipped  = $skipped
        input_tokens   = if ($haveUsage) { ($envelope.modelUsage.PSObject.Properties.Value.inputTokens  | Measure-Object -Sum).Sum } else { $null }
        output_tokens  = if ($haveUsage) { ($envelope.modelUsage.PSObject.Properties.Value.outputTokens | Measure-Object -Sum).Sum } else { $null }
        cost_usd       = if ($haveUsage) { ($envelope.modelUsage.PSObject.Properties.Value.costUSD      | Measure-Object -Sum).Sum } else { $null }
        duration_ms    = $durationMs
        verdict        = $verdict
    }
    if ($source) { $row['source'] = $source }
    try { Add-Content -LiteralPath $logPath -Value ($row | ConvertTo-Json -Depth 4 -Compress) }
    catch { [Console]::Error.WriteLine("advisor-bridge: could not append to $logPath") }
}

# --- 9. Pre-spawn guard ----------------------------------------------------
# Key-set EQUALITY, not "contains no ANTHROPIC_*". The Problem section names
# CLAUDE_CODE_SUBAGENT_MODEL as part of the same leak and a prefix check passes
# it untouched; so would any future CLAUDE_* or provider variable an Ollama
# release adds. The whitelist is already enumerated, so equality costs nothing
# and closes the whole family rather than one prefix of it.
$actual   = @($psi.Environment.Keys) | Sort-Object
$expected = @($ENV_WHITELIST | Where-Object {
    switch ($_) {
        'CLAUDE_EFFORT' { $true }          # always set explicitly above
        'HOME'          { [bool]$homeDir } # derived above, not inherited
        default         { $null -ne [Environment]::GetEnvironmentVariable($_) }
    }
}) | Sort-Object
if (($actual -join ',') -ne ($expected -join ',')) {
    $extra   = @($actual   | Where-Object { $_ -notin $expected })
    $missing = @($expected | Where-Object { $_ -notin $actual })
    Write-LogRow 'model_guard' $null 0 $null
    Fail "child environment does not match the whitelist (extra: $($extra -join ',') | missing: $($missing -join ','))" 2
}
```

Exit 2, not 1: this is not a configuration mistake the user can fix by editing a file — it means the environment scrub itself is broken, which is the same "do not trust this result" class as the post-run guard. It writes a `model_guard` row before exiting, because the spec's exit-2 table assigns this guard that verdict and a verdict has nowhere to live except a row.

- [ ] **Step 5: Relocate and widen `-DryRun`**

`-DryRun` already exists — Task 4 added it and Task 5 gave it the budgeted values.
**Move that block from where Task 5 left it to here**, below the environment build
and the pre-spawn guard, and widen it with `env`, `args`, `cwd`, `exe` and `model`.
There must be exactly one `if ($DryRun)` block in the finished script: leaving the
earlier one in place would exit before the environment is ever built, and every
assertion in `Env.Tests.ps1` would read a JSON object with no `env` key.

Placing it after the guard is deliberate — the guard's exit 2 must be reachable in a
dry run, since the environment-scrub tests are the only thing that exercises it.

```powershell
# --- -DryRun ---------------------------------------------------------------
# A deliverable seam, not a test-only afterthought: the environment-scrub and
# render assertions cannot exist without it. It is deliberately OUTSIDE the
# stdout/exit contract - it prints JSON and exits 0 without spawning, which is
# not "advice returned" in the sense of the exit table.
if ($DryRun) {
    $envOut = [ordered]@{}
    foreach ($k in (@($psi.Environment.Keys) | Sort-Object)) { $envOut[$k] = $psi.Environment[$k] }
    [ordered]@{
        env            = $envOut
        args           = @($psi.ArgumentList)
        cwd            = $psi.WorkingDirectory
        exe            = $claudeExe
        model          = $model
        render         = $rendered
        chars_sent     = $charsSent
        turns_rendered = $turnsRendered
        turns_elided   = $elided
        lines_skipped  = $skipped
    } | ConvertTo-Json -Depth 8
    exit 0
}

# --- 10. Scratch directory -------------------------------------------------
# The child needs no repository access - it has no tools - and running it in the
# caller's cwd would file its transcript in the caller's project directory,
# where the next `claude --continue` could resume the advisor instead of the
# user's own session.
if (-not (Test-Path -LiteralPath $scratchDir)) {
    try { New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null }
    catch { Fail "could not create scratch directory: $scratchDir" }
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Env.Tests.ps1 -Output Detailed`
Expected: PASS, 7 tests.

Then re-run the whole suite — relocating `-DryRun` moved it past the executable
resolution, the persona preflight and the guard, so a render test that passed in
Task 5 can now fail on a preflight that fires first. This is the run that catches it.

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/tests/Env.Tests.ps1
git commit -m "feat(advisor-bridge): child environment built from empty, guarded by key-set equality"
```

---

### Task 7: Spawn, timeout, envelope classification, model guard, logging

The last of the engine, and the part that spends money.

**Files:**
- Modify: `advisor-bridge/scripts/advisor-bridge.ps1` (append)
- Test: `advisor-bridge/tests/Guard.Tests.ps1`
- Create: `advisor-bridge/tests/fixtures/envelope-wrong-model.json`
- Create: `advisor-bridge/tests/fixtures/envelope-two-models.json`
- Create: `advisor-bridge/tests/fixtures/envelope-no-modelusage.json`
- Create: `advisor-bridge/tests/fixtures/envelope-ok.json`

**Interfaces:**
- Consumes: everything above.
- Produces: the terminal behaviour — stdout, log rows, exit codes; `-EnvelopeFile` and `-TimeoutSec` seams.

- [ ] **Step 1: Capture the real envelope shape FIRST**

**This is a prerequisite, not a nice-to-have.** The spike recorded token counts but never recorded `modelUsage`'s actual shape. A guard written against an assumed shape reads a key that does not exist, gets `$null`, and `$null -eq $null` passes — a fail-closed guard silently inverted to fail-open, which is worse than no guard because the whole design leans on it.

```powershell
'say ok' | claude -p --model claude-fable-5-1 --output-format json --tools "" `
    --strict-mcp-config --setting-sources "" |
    Tee-Object -FilePath "$env:TEMP\envelope.json" |
    ConvertFrom-Json | Select-Object -ExpandProperty modelUsage | ConvertTo-Json -Depth 6
```

Record the verbatim shape in the spec's `### Guards` section (an object keyed by model id? a list? nested?) and commit that spec edit. Then build the four fixtures below from that shape. **If the shape differs from an object keyed by model id, adjust the guard code in Step 4.**

- [ ] **Step 2: Write the failing tests**

`advisor-bridge/tests/Guard.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'

    function Invoke-WithEnvelope {
        param([string]$Envelope, [string[]]$Extra = @())
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $ef = Join-Path $script:Fixtures $Envelope
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -EnvelopeFile '$ef' $($Extra -join ' ')
            exit `$LASTEXITCODE" 2>&1
        $log = Join-Path $h 'advisor-bridge.log.jsonl'
        [pscustomobject]@{
            Code = $LASTEXITCODE
            Text = ($out -join "`n")
            Rows = if (Test-Path $log) { @(Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() }
        }
    }
}

Describe 'post-run model guard' {
    It 'exits 2 and prints nothing when the envelope names another model' {
        $r = Invoke-WithEnvelope 'envelope-wrong-model.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'trips on a mixed envelope, because membership is not enough' {
        $r = Invoke-WithEnvelope 'envelope-two-models.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'trips when modelUsage is absent entirely rather than passing on a null compare' {
        $r = Invoke-WithEnvelope 'envelope-no-modelusage.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'passes the right model through and marks the row as canned' {
        $r = Invoke-WithEnvelope 'envelope-ok.json'
        $r.Code | Should -Be 0
        $r.Text | Should -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'ok'
        $r.Rows[-1].source  | Should -Be 'envelope-file'
    }
}

Describe 'the other terminal verdicts' {
    # Without these three, child_error and no_envelope - two of the five
    # documented verdicts - are never reached by any test, and the precedence
    # rule lives only in a comment.
    It 'reports child_error when the envelope says is_error' {
        $r = Invoke-WithEnvelope 'envelope-child-error.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'child_error'
    }
    It 'lets model_guard beat child_error when both apply' {
        $r = Invoke-WithEnvelope 'envelope-error-and-wrong-model.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'reports no_envelope when nothing parses as a result envelope' {
        $r = Invoke-WithEnvelope 'envelope-malformed.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'no_envelope'
    }
}

Describe 'log row' {
    It 'carries all twelve documented fields' {
        $r = Invoke-WithEnvelope 'envelope-ok.json'
        foreach ($f in $script:LogFields) {
            $r.Rows[-1].PSObject.Properties.Name | Should -Contain $f
        }
    }
    It 'records real token and cost figures on a model_guard trip, not nulls' {
        # The call was billed. Nulling the cost here would hide real spend in
        # the one column `## Cost` calibrates from.
        $r = Invoke-WithEnvelope 'envelope-two-models.json'
        $r.Rows[-1].verdict  | Should -Be 'model_guard'
        $r.Rows[-1].cost_usd | Should -Not -BeNullOrEmpty
    }
}
```

Add the shared field list to the `BeforeAll`, so the timeout test below asserts the
same twelve fields rather than a hand-picked three:

```powershell
    $script:LogFields = @('ts','session_id','model','chars_sent','turns_rendered',
                          'turns_elided','lines_skipped','input_tokens','output_tokens',
                          'cost_usd','duration_ms','verdict')
```

- [ ] **Step 3: Build the four envelope fixtures**

From the shape recorded in Step 1. Assuming `modelUsage` is an object keyed by model id:

```json
// envelope-ok.json
{"type":"result","is_error":false,"result":"SECRET-ADVICE-BODY",
 "modelUsage":{"claude-fable-5-1":{"inputTokens":1414,"outputTokens":4,"costUSD":0.029}},
 "duration_ms":64000}
```

```json
// envelope-wrong-model.json
{"type":"result","is_error":false,"result":"SECRET-ADVICE-BODY",
 "modelUsage":{"glm-5.3-flash:cloud":{"inputTokens":10,"outputTokens":4,"costUSD":0}},
 "duration_ms":900}
```

```json
// envelope-two-models.json
{"type":"result","is_error":false,"result":"SECRET-ADVICE-BODY",
 "modelUsage":{"claude-fable-5-1":{"inputTokens":1414,"outputTokens":4,"costUSD":0.029},
               "claude-haiku-4-5-20251001":{"inputTokens":80,"outputTokens":2,"costUSD":0.001}},
 "duration_ms":64000}
```

```json
// envelope-no-modelusage.json
{"type":"result","is_error":false,"result":"SECRET-ADVICE-BODY","duration_ms":900}
```

Three more, because otherwise two of the five documented verdicts are never
reached by any test and the documented precedence rule is asserted only in prose:

```json
// envelope-child-error.json — is_error true, model correct: proves child_error fires
{"type":"result","is_error":true,"result":"SECRET-ADVICE-BODY",
 "modelUsage":{"claude-fable-5-1":{"inputTokens":1414,"outputTokens":4,"costUSD":0.029}},
 "duration_ms":64000}
```

```json
// envelope-error-and-wrong-model.json — both conditions hold, so it pins the
// precedence: model_guard must win over child_error.
{"type":"result","is_error":true,"result":"SECRET-ADVICE-BODY",
 "modelUsage":{"glm-5.3-flash:cloud":{"inputTokens":10,"outputTokens":4,"costUSD":0}},
 "duration_ms":900}
```

`envelope-malformed.json` is not JSON at all — write the literal bytes
`{ this is not an envelope` — so the `-EnvelopeFile` parse fails and `no_envelope`
is reached.

- [ ] **Step 4: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Guard.Tests.ps1 -Output Detailed`
Expected: FAIL — nothing spawns or guards yet.

- [ ] **Step 5: Append spawn, timeout and classification**

```powershell
# --- 11. Spawn -------------------------------------------------------------
$sw       = [System.Diagnostics.Stopwatch]::StartNew()
$verdict  = 'ok'
$envelope = $null
$exitCode = 0
$source   = $null

if ($EnvelopeFile) {
    # A deliverable test seam, outside the stdout/exit contract. It writes
    # source='envelope-file' into its log row so a canned row can never be read
    # as a billed one by the cost calibration or the manual e2e check.
    $source = 'envelope-file'
    try { $envelope = Get-Content -Raw -LiteralPath $EnvelopeFile | ConvertFrom-Json }
    catch { $envelope = $null }
    if ($null -eq $envelope) { $verdict = 'no_envelope' }
}
else {
    $proc = [System.Diagnostics.Process]::Start($psi)

    # Start draining stdout and stderr BEFORE writing stdin. The rendered
    # transcript can be 80 KB; if the child fills its stdout pipe while we are
    # still writing stdin and nobody is reading, both sides block forever and
    # only the timeout breaks it.
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    # The child can die before it ever reads stdin - a rejected argument, a
    # missing credential - and this Write then raises an IOException on a broken
    # pipe. Unguarded, under $ErrorActionPreference = 'Stop', that kills the
    # wrapper before any log row and with an exit code outside the published
    # table. Same hazard and same remedy as the renderer's per-line try/catch.
    #
    # WriteAsync, not Write: the timeout only arms at WaitForExit BELOW, so a
    # synchronous write is outside its cover. A child that neither reads stdin
    # nor exits blocks forever once the 80 KB render passes the pipe buffer -
    # the exact hang the timeout exists for, in the one window the timeout does
    # not watch.
    $writeFailed = $false
    try {
        $writeTask = $proc.StandardInput.WriteAsync($rendered)
        if (-not $writeTask.Wait($timeoutSeconds * 1000)) {
            try { $proc.Kill($true) } catch { }
            $verdict = 'timeout'
        }
        else { $proc.StandardInput.Close() }
    }
    catch { $writeFailed = $true }

    if ($verdict -eq 'timeout') { }   # already killed above; skip the wait
    elseif (-not $proc.WaitForExit($timeoutSeconds * 1000)) {
        # Kill($true) takes the whole process tree. `claude` on Windows launches
        # a node child, and killing only the parent leaves it holding the pipe.
        try { $proc.Kill($true) } catch { }
        $verdict = 'timeout'
    }
    else {
        $exitCode  = $proc.ExitCode
        $stdoutRaw = $stdoutTask.GetAwaiter().GetResult()
        $stderrRaw = $stderrTask.GetAwaiter().GetResult()
        if ($stderrRaw) { [Console]::Error.Write($stderrRaw) }

        $line = $stdoutRaw -split "`n" | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
        if ($line) {
            try { $envelope = $line | ConvertFrom-Json } catch { $envelope = $null }
            if ($envelope.type -ne 'result') { $envelope = $null }
        }

        # --- 12. Classify, in this precedence ------------------------------
        # timeout beats a nonzero exit (a killed child also exits nonzero, and
        # timeout is the more specific fact); then child_error; then
        # no_envelope.
        if ($writeFailed)                     { $verdict = 'child_error' }
        elseif ($envelope.is_error -eq $true) { $verdict = 'child_error' }
        elseif ($exitCode -ne 0)              { $verdict = 'child_error' }
        elseif ($null -eq $envelope)          { $verdict = 'no_envelope' }
    }
}
$sw.Stop()
```

- [ ] **Step 6: Append the model guard, logging and exits**

```powershell
# --- 13. Post-run model guard ----------------------------------------------
# Set EQUALITY, not membership. Membership would pass a mixed envelope, which is
# the shape a fallback or a retry against a different model produces - the exact
# case this guard is for. model_guard beats child_error wherever both could
# apply: a reply from the wrong model is what the caller must not act on.
#
# This guard is what makes the whole design safe. Without it the failure mode
# the bridge exists to prevent - GLM advising GLM - returns silently, formatted
# as advice.
#
# The `$envelope` non-null test is load-bearing, not defensive. A timeout, a
# broken stdin pipe or an auth failure leaves no envelope at all; without this
# test `$used` would be empty, `$used.Count -ne 1` would hold, and EVERY such
# failure would be relabelled `model_guard` - so the log column and the message
# the caller sees would both report "the wrong model answered" for a call in
# which no model answered. `no_envelope` and `child_error` already carry those
# cases and already exit 2. The guard only decides between models when a reply
# actually arrived.
if ($envelope -and $verdict -in 'ok', 'child_error') {
    $used = @()
    if ($envelope.PSObject.Properties.Name -contains 'modelUsage' -and $envelope.modelUsage) {
        $used = @($envelope.modelUsage.PSObject.Properties.Name)
    }
    # An envelope that arrived but named no model is still a guard trip: the
    # reply is real and its provenance is unverifiable, which is the one thing
    # the caller must not act on.
    if ($used.Count -ne 1 -or $used[0] -ne $model) {
        $verdict = 'model_guard'
    }
}

# --- 14. Log row -----------------------------------------------------------
# Written on every exit-0 and exit-2 path, through the same Write-LogRow the
# pre-spawn guard uses. Exit-1 paths write none: nothing was attempted, there is
# no verdict to record, and a row per disabled-gate call would swamp the cost
# column the calibration reads.
#
# Note what Write-LogRow does NOT do: it does not null the token and cost fields
# just because the verdict is not 'ok'. A model_guard trip and an
# envelope-bearing child_error were both really billed, and nulling them there
# would make the cost column under-report real spend on exactly the guard path.
# The nulls distinguish "no envelope came back" from "free", which is a question
# about the envelope, not the verdict.
Write-LogRow $verdict $envelope ([int]$sw.ElapsedMilliseconds) $source

# --- 15. Output and exit ---------------------------------------------------
if ($verdict -ne 'ok') {
    [Console]::Error.WriteLine("advisor-bridge: advisor call failed ($verdict) - reply discarded")
    exit 2
}
# stdout carries the envelope's result text and nothing else. The caller is a
# model reading advice, not a JSON parser.
Write-Output $envelope.result
exit 0
```

- [ ] **Step 7: Write the timeout test**

Append to `advisor-bridge/tests/Guard.Tests.ps1`:

```powershell
Describe 'timeout' {
    It 'kills a slow child, exits 2, logs null cost and a real duration' {
        $stub = Join-Path ([System.IO.Path]::GetTempPath()) "ab-stub-$([guid]::NewGuid()).cmd"
        Set-Content -LiteralPath $stub -Value "@echo off`r`nping -n 12 127.0.0.1 >nul"
        # Point Get-ClaudePath at the stub by putting its directory first on PATH
        # under the name claude.cmd.
        $dir = Join-Path ([System.IO.Path]::GetTempPath()) "ab-bin-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Move-Item $stub (Join-Path $dir 'claude.cmd')

        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $PSScriptRoot 'fixtures' 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')

        $start = Get-Date   # anchor for the orphan-process check below
        $out = & pwsh -NoProfile -Command "
            `$env:PATH = '$dir;' + `$env:PATH
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -TimeoutSec 2
            exit `$LASTEXITCODE" 2>&1

        $LASTEXITCODE | Should -Be 2
        $row = (Get-Content (Join-Path $h 'advisor-bridge.log.jsonl') | Select-Object -Last 1) | ConvertFrom-Json
        $row.verdict     | Should -Be 'timeout'
        $row.cost_usd    | Should -BeNullOrEmpty
        $row.duration_ms | Should -BeGreaterThan 1500
        $row.duration_ms | Should -BeLessThan 8000

        # The spec requires the twelve fields on the TIMEOUT path too, not only
        # on success - a row that silently lost half its columns when the call
        # failed would be worst exactly where it is most needed.
        foreach ($f in $script:LogFields) {
            $row.PSObject.Properties.Name | Should -Contain $f
        }

        # No orphaned child. Kill($true) takes the whole tree because `claude` on
        # Windows launches a further process; a regression to a parent-only
        # Kill() leaves that grandchild running and holding the pipe, and every
        # assertion above still passes. This is the only check that catches it.
        # The stub's grandchild is `ping`, started after the run began.
        Start-Sleep -Milliseconds 500
        @(Get-Process -Name 'PING' -ErrorAction SilentlyContinue |
            Where-Object { $_.StartTime -gt $start }).Count | Should -Be 0

        # And no leftover temp files: the wrapper writes none on this path.
        @(Get-ChildItem -LiteralPath $h -Filter '*.tmp' -ErrorAction SilentlyContinue).Count |
            Should -Be 0
    }
}
```

- [ ] **Step 8: Run the whole suite**

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add advisor-bridge/scripts/advisor-bridge.ps1 advisor-bridge/tests/Guard.Tests.ps1 advisor-bridge/tests/fixtures
git commit -m "feat(advisor-bridge): spawn, timeout, model guard, and the run log"
```

---

### Task 8: The persona

The highest-leverage artifact in the package, and the cheapest to iterate on — it is a file, not code.

**Files:**
- Create: `advisor-bridge/advisor-bridge-persona.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the file `install.ps1` copies to `~/.claude/advisor-bridge-persona.md`.

- [ ] **Step 1: Write the persona**

`advisor-bridge/advisor-bridge-persona.md`:

```markdown
You are a senior reviewer reading another model's working transcript. It sent
you everything it has done so far and one implicit question: what should it do
next?

Answer in four moves, in this order.

**1. Say where the caller actually is.** One line. Orienting (still gathering
facts), committing (about to pick an approach), stuck (repeating a failure), or
declaring done. The useful advice differs completely between these, and the
caller frequently misjudges which one it is in.

**2. Diagnose from what it actually tried**, not from what the task sounds like
it needs. Quote the transcript — the command it ran, the error it got, the file
it read. If the transcript does not contain evidence for a claim, do not make
the claim. Generic advice that would fit any session is worse than nothing here,
because it costs the caller a real API call to receive.

**3. Give the discriminating check, not the verdict.** Name the one command,
file, or test that separates the two live hypotheses. "Run X; if it prints Y the
cause is A, if it prints Z the cause is B" beats "the cause is probably A."

**4. Say what blocks and what does not.** End with an explicit split: concerns
that should stop the caller now, and concerns worth noting and moving past. An
advisor that flags everything at equal weight makes the next decision harder,
not easier.

Be terse. The caller is a model with a token budget, not a reader. No preamble,
no summary of what it already knows, no encouragement. If the caller is on the
right track, say so in one line and spend the rest on the single weakest point.

If the transcript is truncated — it will say so in its header — reason from what
is there and say which missing piece would change your answer.
```

- [ ] **Step 2: Behavioural acceptance (manual)**

Acceptance for this file is behavioural, not a unit test. Run it against a captured transcript of a genuinely stuck session and check the reply names a **next action**, not a summary.

**That transcript is a local, uncommitted artifact.** It is not the golden fixture and must never become one: real transcripts carry absolute paths, the user's email and machine details, and this repo is public. Keep it under `%TEMP%`.

```powershell
# after install, with the bridge enabled
pwsh -NoProfile -File "$HOME/.claude/scripts/advisor-bridge.ps1"
```

Judge: does the reply open by classifying the caller's position, quote something from the transcript, and name one concrete check? If it returns generic encouragement, edit this file — not the script — and retry.

- [ ] **Step 3: Commit**

```bash
git add advisor-bridge/advisor-bridge-persona.md
git commit -m "feat(advisor-bridge): advisor persona"
```

---

### Task 9: SessionStart hook

**Files:**
- Create: `advisor-bridge/hooks/advisor-bridge-status.py`
- Test: `advisor-bridge/tests/Hook.Tests.ps1`

**Interfaces:**
- Consumes: `~/.claude/advisor-bridge.json` (via an `ADVISOR_BRIDGE_HOME` override for tests).
- Produces: `additionalContext` on stdout, or silence.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Hook.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Hook = Join-Path $PSScriptRoot '..' 'hooks' 'advisor-bridge-status.py'
    # $Config is UNTYPED: a [string] parameter coerces $null to '', so
    # `if ($null -ne $Config)` would always be true and the "config is absent"
    # case below would silently become "config is empty" - a different branch.
    function Invoke-Hook {
        param($Config, [string]$BaseUrl)
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        if ($null -ne $Config) { Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value $Config }
        $out = '{}' | & pwsh -NoProfile -Command "
            `$env:ADVISOR_BRIDGE_HOME = '$h'
            `$env:ANTHROPIC_BASE_URL = '$BaseUrl'
            `$input | python '$script:Hook'" 2>&1
        ($out -join "`n")
    }
}

Describe 'hook gating' {
    It 'is silent when the config is absent' {
        # Genuinely absent, not empty - see the untyped $Config above.
        Invoke-Hook -Config $null -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent when the config is unreadable' {
        Invoke-Hook -Config '{ not json' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent when disabled, even in an Ollama session' {
        Invoke-Hook -Config '{"enabled": false}' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent in an Anthropic session even when enabled' {
        Invoke-Hook -Config '{"enabled": true}' -BaseUrl 'https://api.anthropic.com' | Should -BeNullOrEmpty
    }
    It 'is silent when ANTHROPIC_BASE_URL is unset' {
        Invoke-Hook -Config '{"enabled": true}' -BaseUrl '' | Should -BeNullOrEmpty
    }
    It 'injects the protocol when enabled and the backend is not Anthropic' {
        $out = Invoke-Hook -Config '{"enabled": true}' -BaseUrl 'http://127.0.0.1:11434'
        $out | Should -Match 'advisor-bridge'
        $out | Should -Match 'timeout'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Hook.Tests.ps1 -Output Detailed`
Expected: FAIL — the hook does not exist.

- [ ] **Step 3: Write the hook**

`advisor-bridge/hooks/advisor-bridge-status.py`:

```python
"""SessionStart hook: teach a non-Anthropic-backed session that it can still
reach an Anthropic advisor, but only when the bridge is switched on.

Two gates, and neither substitutes for the other. The config gate comes FIRST:
base-URL alone would inject the protocol into every Ollama session while the
bridge is off, spending context on every session and steering the model into
calls that exit 1 - so turning the bridge off would not turn its surface off.
The base-URL gate keeps the protocol out of sessions that already have the
native advisor tool.

Silent on all three config branches, which is a deliberate divergence from
ollama-workers-status.py:79 - that hook prints on its unreadable branch, but it
also checks nothing before doing so. This one runs its config read before the
base-URL check, so a print there would land in every Anthropic session too.
"""
import json
import os
import sys
from urllib.parse import urlparse

HOME = os.environ.get("ADVISOR_BRIDGE_HOME") or os.path.join(
    os.path.expanduser("~"), ".claude"
)
CONFIG_PATH = os.path.join(HOME, "advisor-bridge.json")

PROTOCOL = """You have an advisor: a stronger reviewer that sees this session's full
transcript. This session's backend is not Anthropic, so the built-in advisor tool is
unavailable; the bridge below reaches one anyway, in a separate process.

Call it BEFORE substantive work - before writing, before committing to an
interpretation, before building on an assumption. Orientation (finding files, reading
what is there) is not substantive work; writing, editing and declaring an answer are.
Also call it when stuck, when considering a change of approach, and when you believe
the task is complete - making the deliverable durable first.

    Bash(command: "pwsh -NoProfile -File ~/.claude/scripts/advisor-bridge.ps1",
         timeout: 300000)

The timeout is not optional. A real transcript takes well over the 120s default, and
without it you see a killed call instead of advice.

Weigh the answer: primary-source evidence in your own transcript outranks the advice.
If they genuinely conflict, make one reconciling call rather than switching silently."""


def main():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        sys.exit(0)

    if cfg.get("enabled") is not True:
        sys.exit(0)

    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        sys.exit(0)
    try:
        host = urlparse(base).hostname or ""
    except ValueError:
        sys.exit(0)
    if host == "api.anthropic.com":
        sys.exit(0)

    print(PROTOCOL)


main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Hook.Tests.ps1 -Output Detailed`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add advisor-bridge/hooks/advisor-bridge-status.py advisor-bridge/tests/Hook.Tests.ps1
git commit -m "feat(advisor-bridge): SessionStart hook gated on config then backend"
```

---

### Task 10: SKILL.md

**Files:**
- Create: `advisor-bridge/SKILL.md`
- Test: `advisor-bridge/tests/Skill.Tests.ps1`

**Interfaces:**
- Consumes: nothing.
- Produces: the file `install.ps1` copies to `~/.claude/skills/advisor-bridge/SKILL.md`.

At the package **root**, not `skills/advisor-bridge/SKILL.md` — the repo keeps it flat and the installer builds the nesting, exactly as `ollama-workers/install.ps1:29` does.

- [ ] **Step 1: Write the failing test**

`advisor-bridge/tests/Skill.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll { $script:Skill = Get-Content -Raw (Join-Path $PSScriptRoot '..' 'SKILL.md') }

Describe 'SKILL.md' {
    It 'passes timeout: 300000 on the invocation line' {
        # The failure most likely to spoil first use: a real transcript exceeds
        # the Bash tool's 120s default and the caller sees a kill, not advice.
        $script:Skill | Should -Match 'timeout:\s*300000'
    }
    It 'documents on, off and status' {
        $script:Skill | Should -Match '(?m)^\*\*`on`'
        $script:Skill | Should -Match '(?m)^\*\*`off`'
        $script:Skill | Should -Match '(?m)^\*\*`status`'
    }
    It 'has frontmatter with a name and a description' {
        $script:Skill | Should -Match '(?s)^---.*\nname: advisor-bridge\n.*description: .*\n---'
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `Invoke-Pester advisor-bridge/tests/Skill.Tests.ps1 -Output Detailed`
Expected: FAIL — no `SKILL.md`.

- [ ] **Step 3: Write SKILL.md**

`advisor-bridge/SKILL.md`:

````markdown
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
````

- [ ] **Step 4: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests/Skill.Tests.ps1 -Output Detailed`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add advisor-bridge/SKILL.md advisor-bridge/tests/Skill.Tests.ps1
git commit -m "feat(advisor-bridge): skill surface with the mandatory Bash timeout"
```

---

### Task 11: Installer, and the ollama-workers back-fill

This is the first time two packages in this repo append to the same `SessionStart` category, and the sibling's verifier cannot detect the failure that creates.

**Files:**
- Create: `advisor-bridge/install.ps1`
- Modify: `ollama-workers/install.ps1:16-21` (add the `-ClaudeHome` seam), `:125-131` (guard the null-`hooks` loop), `:120-140` (the verifier block)
- Test: `advisor-bridge/tests/Install.Tests.ps1`

**Interfaces:**
- Consumes: every file from Tasks 2, 8, 9, 10.
- Produces: files under `~/.claude`, a seeded config, and one registered SessionStart hook.

- [ ] **Step 1: Write the failing tests**

`advisor-bridge/tests/Install.Tests.ps1`:

```powershell
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }

BeforeAll {
    $script:Install = Join-Path $PSScriptRoot '..' 'install.ps1'

    function New-Fake-ClaudeHome([string]$settings) {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("abi-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        if ($settings) { Set-Content -LiteralPath (Join-Path $h 'settings.json') -Value $settings }
        return $h
    }
    $script:SiblingSettings = @'
{
  "model": "opus[1m]",
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "python ~/.claude/hooks/ollama-workers-status.py", "timeout": 10 } ] } ],
    "Stop": [ { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/keep-awake.sh stop" } ] } ]
  }
}
'@
}

Describe 'installer' {
    It 'dry-run prints the pre-existing SessionStart commands and the one it would add' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        $out = & pwsh -NoProfile -File $script:Install -ClaudeHome $h -DryRun 2>&1
        $json = ($out -join "`n") | Select-String -Pattern '(?s)\{.*\}' | ForEach-Object { $_.Matches[0].Value }
        $plan = $json | ConvertFrom-Json
        $plan.existing_session_start | Should -Contain 'python ~/.claude/hooks/ollama-workers-status.py'
        $plan.adding                 | Should -Match 'advisor-bridge-status'
    }
    It 'dry-run writes nothing' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        $before = Get-Content -Raw (Join-Path $h 'settings.json')
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h -DryRun | Out-Null
        (Get-Content -Raw (Join-Path $h 'settings.json')) | Should -Be $before
        Join-Path $h 'scripts' 'advisor-bridge.ps1' | Should -Not -Exist
    }
    It 'places every file and seeds the config disabled' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        Join-Path $h 'skills' 'advisor-bridge' 'SKILL.md'   | Should -Exist
        Join-Path $h 'scripts' 'advisor-bridge.ps1'         | Should -Exist
        Join-Path $h 'hooks' 'advisor-bridge-status.py'     | Should -Exist
        Join-Path $h 'advisor-bridge-persona.md'            | Should -Exist
        ((Get-Content -Raw (Join-Path $h 'advisor-bridge.json')) | ConvertFrom-Json).enabled | Should -BeFalse
    }
    It 'keeps the ollama-workers SessionStart entry alongside its own' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $cmds = @((Get-Content -Raw (Join-Path $h 'settings.json') | ConvertFrom-Json).hooks.SessionStart.hooks.command)
        $cmds | Should -Contain 'python ~/.claude/hooks/ollama-workers-status.py'
        ($cmds -join ' ') | Should -Match 'advisor-bridge-status'
    }
    It 'preserves unrelated hook categories and top-level keys' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $s = Get-Content -Raw (Join-Path $h 'settings.json') | ConvertFrom-Json
        $s.model | Should -Be 'opus[1m]'
        @($s.hooks.Stop.hooks.command) | Should -Contain 'bash ~/.claude/hooks/keep-awake.sh stop'
    }
    It 'does not seed over an existing config' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true, "model": "mine"}'
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        ((Get-Content -Raw (Join-Path $h 'advisor-bridge.json')) | ConvertFrom-Json).model | Should -Be 'mine'
    }
    It 'is idempotent — a second run adds no duplicate entry' {
        $h = New-Fake-ClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $cmds = @((Get-Content -Raw (Join-Path $h 'settings.json') | ConvertFrom-Json).hooks.SessionStart.hooks.command)
        @($cmds | Where-Object { $_ -like '*advisor-bridge-status*' }).Count | Should -Be 1
    }

    It 'restores the backup and throws when the rewrite loses data' {
        # The rollback branch is the entire safety net for rewriting a
        # settings.json two packages now share, and every other test here is a
        # happy path - a $lost check that never fires, or a restore that does
        # not restore, would leave all of them green.
        #
        # The loss is induced without stubbing anything, using the exact failure
        # the -Depth 100 comment names: past that maximum ConvertTo-Json emits
        # the remainder as a type name and only WARNS, which
        # $ErrorActionPreference does not catch. A hook category nested deeper
        # than 100 therefore round-trips to something different, the comparison
        # sees it, and the rollback fires.
        $deep = '{"type":"command","command":"bash x"}'
        for ($i = 0; $i -lt 120; $i++) { $deep = '{"n":' + $deep + '}' }
        $settings = @"
{
  "model": "opus[1m]",
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "python ~/.claude/hooks/ollama-workers-status.py", "timeout": 10 } ] } ],
    "Deep": $deep
  }
}
"@
        $h      = New-Fake-ClaudeHome $settings
        $before = Get-Content -Raw (Join-Path $h 'settings.json')
        $out    = & pwsh -NoProfile -File $script:Install -ClaudeHome $h 2>&1

        $LASTEXITCODE | Should -Not -Be 0
        ($out -join "`n") | Should -Match 'restored from'
        (Get-Content -Raw (Join-Path $h 'settings.json')) | Should -Be $before
    }

    It 'survives a settings.json with no hooks key at all' {
        # $before.hooks is $null here. An unguarded `foreach ($cat in
        # @($before.hooks.Keys))` indexes a null array and throws AFTER the
        # rewrite has landed and BEFORE the rollback, so the installer would die
        # with the damaged file in place and never name the backup.
        $h = New-Fake-ClaudeHome '{ "model": "opus[1m]" }'
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $LASTEXITCODE | Should -Be 0
        $s = Get-Content -Raw (Join-Path $h 'settings.json') | ConvertFrom-Json
        $s.model | Should -Be 'opus[1m]'
        @($s.hooks.SessionStart.hooks.command) | Should -Match 'advisor-bridge-status'
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `Invoke-Pester advisor-bridge/tests/Install.Tests.ps1 -Output Detailed`
Expected: FAIL — no installer.

- [ ] **Step 3: Write the installer**

`advisor-bridge/install.ps1`:

```powershell
#Requires -Version 7
<#
.SYNOPSIS
Installs the advisor-bridge package and wires up the pieces a plain `cp -r`
cannot: the engine script, the persona, the config seed, and the SessionStart
hook.

.DESCRIPTION
Idempotent. Existing config is left alone. settings.json is re-serialized to add
one SessionStart hook entry, after a timestamped backup, and the rewrite is
verified before it is kept.

The verification differs from ollama-workers/install.ps1 deliberately: this is
the first time two packages in this repo append to the same SessionStart
category, and the sibling's check skips that category wholesale and then
confirms only that its OWN entry landed. Copied verbatim, a rewrite that dropped
the ollama-workers entry would verify clean and keep the damaged file. So every
pre-existing SessionStart command string is collected before the rewrite and
asserted present afterwards.

Run with -DryRun to see the plan without touching anything.
#>
[CmdletBinding()]
param([switch]$DryRun, [string]$ClaudeHome)

$ErrorActionPreference = 'Stop'

$src        = $PSScriptRoot
$claudeHome = if ($ClaudeHome) { $ClaudeHome } else { Join-Path $HOME '.claude' }
$settings   = Join-Path $claudeHome 'settings.json'
$config     = Join-Path $claudeHome 'advisor-bridge.json'

function Step([string]$message) { Write-Host "  $message" }

$copies = @(
    @{ From = 'SKILL.md';                       To = Join-Path $claudeHome 'skills\advisor-bridge\SKILL.md' }
    @{ From = 'scripts\advisor-bridge.ps1';     To = Join-Path $claudeHome 'scripts\advisor-bridge.ps1' }
    @{ From = 'hooks\advisor-bridge-status.py'; To = Join-Path $claudeHome 'hooks\advisor-bridge-status.py' }
    @{ From = 'advisor-bridge-persona.md';      To = Join-Path $claudeHome 'advisor-bridge-persona.md' }
)

# Seeded only when absent: overwriting would discard the user's chosen model,
# budget, or the fact that they turned it on.
$seeds = @(
    @{ From = 'advisor-bridge.example.json'; To = $config }
)

$hookCommand = 'python ~/.claude/hooks/advisor-bridge-status.py'

# Collect BEFORE anything is written - this list is the verification's input.
$existingCommands = @()
if (Test-Path -LiteralPath $settings) {
    try {
        $pre = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
        if ($pre.hooks -and $pre.hooks.ContainsKey('SessionStart')) {
            $existingCommands = @(@($pre.hooks.SessionStart) | ForEach-Object { $_.hooks } |
                ForEach-Object { $_.command } | Where-Object { $_ })
        }
    }
    catch { throw "settings.json is not valid JSON: $settings" }
}

# Validate the package BEFORE the dry-run short-circuit. The sibling runs this
# check unconditionally (ollama-workers/install.ps1:45-49 sits outside the
# `if (-not $DryRun)` that wraps only the copy itself), and a dry run whose whole
# job is "tell me what would happen" must not be the one mode that cannot say
# "a source file is missing".
foreach ($c in $copies) {
    if (-not (Test-Path -LiteralPath (Join-Path $src $c.From))) {
        throw "missing from package: $($c.From)"
    }
}

$alreadyRegistered = [bool](@($existingCommands) | Where-Object { $_ -like '*advisor-bridge-status*' })

if ($DryRun) {
    [ordered]@{
        claude_home            = $claudeHome
        copies                 = @($copies | ForEach-Object { $_.To })
        seeds                  = @($seeds  | ForEach-Object { @{ to = $_.To; action = if (Test-Path -LiteralPath $_.To) { 'keep' } else { 'create' } } })
        existing_session_start = $existingCommands
        # Reports true state on a repeat run, as the sibling's 'already
        # registered' step does. Unconditionally naming the command would tell
        # the user a second install is about to add a duplicate it will not add.
        adding                 = if ($alreadyRegistered) { $null } else { $hookCommand }
        already_registered     = $alreadyRegistered
    } | ConvertTo-Json -Depth 6
    exit 0
}

Write-Host 'advisor-bridge install'
Write-Host 'Files:'
foreach ($c in $copies) {
    $from = Join-Path $src $c.From
    Step "$(if (Test-Path -LiteralPath $c.To) { 'overwrite' } else { 'create' }) $($c.To)"
    $parent = Split-Path -Parent $c.To
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath $from -Destination $c.To -Force
}

Write-Host 'Seeds (kept if they already exist):'
foreach ($s in $seeds) {
    if (Test-Path -LiteralPath $s.To) { Step "keep $($s.To)" }
    else {
        Step "create $($s.To)  (enabled: false - turn on with /advisor-bridge on)"
        Copy-Item -LiteralPath (Join-Path $src $s.From) -Destination $s.To
    }
}

Write-Host 'SessionStart hook:'
if (-not (Test-Path -LiteralPath $settings)) {
    Step "no settings.json at $settings - add this hook yourself: $hookCommand"
}
elseif ($alreadyRegistered) {
    Step 'already registered'
}
else {
    Step 'add entry (settings.json backed up first)'
    $backup = "$settings.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $settings -Destination $backup

    $json  = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
    $hooks = if ($json.ContainsKey('hooks')) { $json.hooks } else { @{} }

    # A generic List, not a PowerShell array: `$x | ForEach-Object {...}` yields
    # a bare object when $x has one element, and that object serialises as a
    # JSON object where Claude Code needs an array.
    $sessionStart = [System.Collections.Generic.List[object]]::new()
    if ($hooks.ContainsKey('SessionStart')) {
        foreach ($group in @($hooks.SessionStart)) { $sessionStart.Add($group) }
    }
    $entry = [System.Collections.Generic.List[object]]::new()
    $entry.Add(@{ type = 'command'; command = $hookCommand; timeout = 10 })
    $sessionStart.Add(@{ hooks = $entry })
    $hooks['SessionStart'] = $sessionStart
    $json['hooks'] = $hooks

    # -Depth 100 is the maximum. Past the limit ConvertTo-Json renders nested
    # objects as their type name and only warns, which $ErrorActionPreference
    # does not catch - hence the verification below.
    $json | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $settings -Encoding utf8

    $before = Get-Content -Raw -LiteralPath $backup | ConvertFrom-Json -AsHashtable
    $lost   = [System.Collections.Generic.List[string]]::new()
    try { $after = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable }
    catch { $after = $null; $lost.Add('file no longer parses as JSON') }

    # The whole comparison is wrapped: it runs AFTER the rewrite is already on
    # disk and BEFORE the rollback below, so any error escaping here kills the
    # installer with the damaged file in place and never even names the backup.
    # Whatever goes wrong, it becomes a $lost entry and the file is restored.
    if ($after) {
        try {
            foreach ($key in $before.Keys) {
                if (-not $after.ContainsKey($key)) { $lost.Add("dropped '$key'"); continue }
                if ($key -eq 'hooks') { continue }
                $b = $before[$key] | ConvertTo-Json -Depth 100 -Compress
                $a = $after[$key]  | ConvertTo-Json -Depth 100 -Compress
                if ($b -ne $a) { $lost.Add("changed '$key'") }
            }
            # Guarded: a settings.json with no `hooks` key at all is ordinary -
            # a file that only sets `model` is enough. Then $before.hooks is
            # $null, @($null.Keys) yields a one-element array holding $null, and
            # $before.hooks[$null] is a terminating error under
            # $ErrorActionPreference = 'Stop'. The sibling has the same shape at
            # ollama-workers/install.ps1:125-131; Step 4 fixes it there too.
            if ($before.hooks) {
                foreach ($cat in @($before.hooks.Keys)) {
                    if ($cat -eq 'SessionStart') { continue }
                    $b = $before.hooks[$cat] | ConvertTo-Json -Depth 100 -Compress
                    $a = $after.hooks[$cat]  | ConvertTo-Json -Depth 100 -Compress
                    if ($b -ne $a) { $lost.Add("changed hook '$cat'") }
                }
            }

            # THE divergence from the sibling: assert EVERY pre-existing command
            # survived, not just our own. Whichever installer runs second is the
            # one that can destroy the other's entry.
            $afterCommands = @(@($after.hooks.SessionStart) | ForEach-Object { $_.hooks } |
                ForEach-Object { $_.command } | Where-Object { $_ })
            foreach ($cmd in $existingCommands) {
                if ($afterCommands -notcontains $cmd) { $lost.Add("dropped SessionStart entry '$cmd'") }
            }
            if (-not ($afterCommands | Where-Object { $_ -like '*advisor-bridge-status*' })) {
                $lost.Add('SessionStart entry was not written')
            }
        }
        catch { $lost.Add("verification failed: $($_.Exception.Message)") }
    }

    if ($lost.Count) {
        Copy-Item -LiteralPath $backup -Destination $settings -Force
        throw "settings.json rewrite lost data ($($lost -join '; ')) - restored from $backup, nothing changed"
    }
    Step "backup at $backup"
}

Write-Host 'Prerequisites:'
$claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claudeExe) { Step "claude found at $claudeExe" }
else { Step 'claude NOT found on PATH - install the Claude Code CLI before enabling' }

Write-Host ''
Write-Host 'Done. Off by default. Enable with /advisor-bridge on.'
```

- [ ] **Step 4: Back-fill the same check into the sibling**

In `ollama-workers/install.ps1`, collect the pre-existing commands before the rewrite (near the top, beside the other path variables) and assert them afterwards. Replace the verifier's own-entry-only check:

```powershell
# BEFORE (ollama-workers/install.ps1, inside the verification block)
$written = @($after.hooks.SessionStart) | ForEach-Object { $_.hooks } |
    Where-Object { $_.command -like '*ollama-workers-status*' }
if (-not $written) { $lost.Add('SessionStart entry was not written') }
```

```powershell
# AFTER
# Every pre-existing entry must survive, not just our own. advisor-bridge also
# appends here now, and whichever installer runs second is the one that can
# destroy the other's entry. Checking only for our own string would verify clean
# on a file that lost theirs.
$afterCommands = @(@($after.hooks.SessionStart) | ForEach-Object { $_.hooks } |
    ForEach-Object { $_.command } | Where-Object { $_ })
foreach ($cmd in $existingCommands) {
    if ($afterCommands -notcontains $cmd) { $lost.Add("dropped SessionStart entry '$cmd'") }
}
if (-not ($afterCommands | Where-Object { $_ -like '*ollama-workers-status*' })) {
    $lost.Add('SessionStart entry was not written')
}
```

Guard the sibling's hook-category loop the same way, for the same reason — a
`settings.json` carrying no `hooks` key makes `@($before.hooks.Keys)` a one-element
array holding `$null`, and indexing with it throws after the rewrite has landed and
before the rollback:

```powershell
# BEFORE (ollama-workers/install.ps1:125-131)
foreach ($cat in @($before.hooks.Keys)) {

# AFTER
if ($before.hooks) {
    foreach ($cat in @($before.hooks.Keys)) {
```

(close the added `if` after that loop's existing closing brace)

And add the collection, immediately after `$overlay` is defined:

```powershell
# Collected before any write; the verification below asserts each one survived.
$existingCommands = @()
if (Test-Path -LiteralPath $settings) {
    try {
        $pre = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
        if ($pre.hooks -and $pre.hooks.ContainsKey('SessionStart')) {
            $existingCommands = @(@($pre.hooks.SessionStart) | ForEach-Object { $_.hooks } |
                ForEach-Object { $_.command } | Where-Object { $_ })
        }
    }
    catch { throw "settings.json is not valid JSON: $settings" }
}
```

- [ ] **Step 5: Give the sibling a `-ClaudeHome` seam so the back-fill is testable**

`ollama-workers/install.ps1` takes only `-DryRun` (`ollama-workers/install.ps1:16`), so
without this the changed verifier can be exercised **only** against the developer's real
`~/.claude` — meaning it could not be exercised at all, and the back-fill would ship
verified by inspection alone. That is the same argument that made `-ClaudeHome` a
deliverable seam in the engine script.

```powershell
# BEFORE (ollama-workers/install.ps1:16-21)
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'

$src        = $PSScriptRoot
$claudeHome = Join-Path $HOME '.claude'

# AFTER
param([switch]$DryRun, [string]$ClaudeHome)

$ErrorActionPreference = 'Stop'

$src        = $PSScriptRoot
$claudeHome = if ($ClaudeHome) { $ClaudeHome } else { Join-Path $HOME '.claude' }
```

Nothing else in that file changes: every path below already derives from `$claudeHome`.

Then add one case to `advisor-bridge/tests/Install.Tests.ps1`, so the sibling's changed
verifier has an actual regression test rather than a reading:

```powershell
Describe 'ollama-workers back-fill' {
    It 'refuses to drop an advisor-bridge SessionStart entry' {
        $sibling = Join-Path $PSScriptRoot '..' '..' 'ollama-workers' 'install.ps1'
        $h = New-Fake-ClaudeHome @'
{
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "python ~/.claude/hooks/advisor-bridge-status.py", "timeout": 10 } ] } ]
  }
}
'@
        & pwsh -NoProfile -File $sibling -ClaudeHome $h | Out-Null
        $cmds = @((Get-Content -Raw (Join-Path $h 'settings.json') | ConvertFrom-Json).hooks.SessionStart.hooks.command)
        # Both entries present: the sibling added its own AND kept ours. Before
        # the back-fill its verifier checked only for its own string, so a
        # rewrite that dropped this one would have verified clean.
        ($cmds -join ' ') | Should -Match 'advisor-bridge-status'
        ($cmds -join ' ') | Should -Match 'ollama-workers-status'
    }
}
```

- [ ] **Step 6: Verify the sibling still installs cleanly**

Run: `pwsh -NoProfile -File ollama-workers/install.ps1 -DryRun`
Expected: the same plan it printed before the change, no errors.

- [ ] **Step 7: Run tests to verify they pass**

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: PASS, whole suite.

- [ ] **Step 8: Commit**

```bash
git add advisor-bridge/install.ps1 advisor-bridge/tests/Install.Tests.ps1 ollama-workers/install.ps1
git commit -m "feat(advisor-bridge): installer that cannot drop the sibling's SessionStart entry"
```

---

### Task 12: README registration and the manual end-to-end procedure

**Files:**
- Modify: `README.md` (index table, and a new "Notes per skill" subsection)
- Create: `advisor-bridge/tests/manual/e2e.md`

**Interfaces:**
- Consumes: everything.
- Produces: the documentation a reader needs to install and run the package.

- [ ] **Step 1: Add the index-table row**

In `README.md`, after the `ollama-workers` row:

```markdown
| [`advisor-bridge`](./advisor-bridge) | Lets a Claude Code session running on a non-Anthropic backend (`ollama launch claude` — GLM, Kimi) reach an Anthropic model for advice, by rendering the session's own transcript into a scrubbed `claude -p` child process. The built-in `advisor` tool is disabled there and would be GLM advising GLM anyway. **Windows-only** (PowerShell). |
```

- [ ] **Step 2: Add the "Notes per skill" subsection**

After the `### ollama-workers` block:

```markdown
### advisor-bridge
- **The inverse of [`ollama-workers`](./ollama-workers).** That package spawns a child
  *away* from Anthropic; this one spawns a child *back to* it. Same reason in both
  cases: one Claude Code process serves exactly one endpoint, so reaching a second
  model means a second process.
- **Needs** Windows with PowerShell 7 (`pwsh`), the `claude` CLI on PATH with an
  Anthropic login, and Python 3 for the SessionStart hook. Run
  `./advisor-bridge/install.ps1` (`-DryRun` first) — a plain `cp -r` installs the skill
  but not the engine script, the persona, the config seed, or the hook.
- **Off by default, and invisible when off.** State lives in
  `~/.claude/advisor-bridge.json` seeded `enabled: false`. The hook reads that config
  *before* it checks the backend, so a disabled install costs no context; and the
  wrapper enforces the same gate itself, exiting 1 before launching anything, so a call
  on stale context after a compact cannot spend money.
- **Two fail-closed guards.** Before spawning, the child's environment key set must
  equal the whitelist exactly — not merely lack `ANTHROPIC_*`, which
  `CLAUDE_CODE_SUBAGENT_MODEL` walks straight through. After the call, the envelope's
  `modelUsage` must equal exactly the configured model; anything else discards the
  reply and exits 2. Without the second guard the failure this package exists to
  prevent — the local model answering in the advisor's voice — returns silently,
  formatted as advice.
- **Costs $0.20–0.40 per call, every call.** Prompt caching matches an exact prefix and
  the rendered transcript is one user message that differs every time, so only the
  ~1.4 K persona amortizes. Read the `cost_usd` column of
  `~/.claude/advisor-bridge.log.jsonl` rather than that estimate; rows carrying
  `"source": "envelope-file"` are canned test runs and cost nothing.
- **Tests:** `Invoke-Pester advisor-bridge/tests -Output Detailed` (needs Pester 5;
  the Windows-bundled Pester 3 will not run them). Offline, deterministic, spends
  nothing. The one paid end-to-end check is a documented manual procedure at
  `advisor-bridge/tests/manual/e2e.md`, deliberately not a `*.Tests.ps1` file so no
  automated glob or CI gate can bill it.
- Invoke `/advisor-bridge on` once, then call it from an Ollama-backed session.
```

- [ ] **Step 3: Write the manual end-to-end procedure**

`advisor-bridge/tests/manual/e2e.md`:

```markdown
# End-to-end check (manual — spends real money)

**Not a `*.Tests.ps1` file, deliberately.** `ship`'s P4 exit gate runs "the change's
own test files"; an end-to-end matching that glob would bill every pipeline run. This
is a procedure a human runs once after install and after any change to the spawn path.

Cost: one real call, roughly $0.20–0.40 depending on transcript size.

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
4. **The advice is advice.** It classifies where the caller is, quotes the transcript,
   and names one concrete next check — see `### Persona` in the spec. Generic
   encouragement means the persona needs editing, not the script.

## If it fails

| Symptom | Look at |
|---|---|
| Exit 1, "disabled or unreadable config" | `~/.claude/advisor-bridge.json` — the seed is `enabled: false` |
| Exit 1, "no transcript for session" | The session had not been written yet; send a message and retry |
| Exit 2, `model_guard` | Something other than the intended advisor answered. Check `modelUsage` in a raw `claude -p --output-format json` call — the guard's shape assumption may be wrong |
| Exit 2, `timeout` | Raise `timeoutSec`; check `duration_ms` in the log row for how close it was |
| Killed with no exit code at all | The Bash tool's timeout fired before the script's. The invocation must pass `timeout: 300000` |
```

- [ ] **Step 4: Verify the whole suite one more time**

Run: `Invoke-Pester advisor-bridge/tests -Output Detailed`
Expected: PASS. Confirm no test file under `tests/manual/` matches `*.Tests.ps1`.

- [ ] **Step 5: Commit**

```bash
git add README.md advisor-bridge/tests/manual/e2e.md
git commit -m "docs(advisor-bridge): README registration and the manual e2e procedure"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: Naming → Global Constraints + Task 11's install paths; Components → Task 11; Order of operations → Tasks 2–7 in that order; Session locator → Task 3; Renderer → Tasks 4–5; Child spawn → Task 6; Persona → Task 8; Guards → Tasks 6 (pre-spawn) and 7 (post-run); Output/logging/exits → Task 7; Skill → Task 10; SessionStart hook → Task 9; Config → Task 2; Cost → Task 10 and Task 12 (the log column, not the estimate); Install → Task 11; Testing → every task's test steps plus Task 12's manual procedure. Both *For the implementer to verify* items are steps, not notes: the `modelUsage` shape is Task 7 Step 1, and the hook-text-inside-user-records question is Task 1 Step 5 — **already settled empirically** (0 of 119 `user` records; every hook and reminder payload lives in a separate `attachment` record), so that step is a re-confirmation with a STOP that fires only if a Claude Code upgrade has changed the layout.

**One deliberate addition beyond the spec.** The spec names four test seams; the plan adds a fifth, `-InjectEnvKey`, honoured only alongside `-DryRun`. The spec's own Testing section asks for the pre-spawn guard to be covered, and it cannot be: `$actual` and `$expected` are both derived from `$ENV_WHITELIST` and the same `GetEnvironmentVariable` calls, so no external input makes them diverge — a guard no test can trip is a guard that could be deleted with the suite still green. Under `-DryRun` nothing spawns and nothing is billed, and the seam is refused outright otherwise. This belongs in the spec's `### Guards` and `## Testing` sections the next time it is opened.

**One spec item is deliberately deferred:** whether SessionStart fires with `source: "compact"`. It cannot be settled without an installed hook, so it is not a task gate — it is checked during Task 12's manual run, and if the hook does fire on compact, no code changes (the hook has no matcher to exclude it).

**Placeholders.** None. Every code step carries the actual code; every test step names the command and the expected result. Two places say "adjust to match what you recorded" — Task 4's property paths and Task 7's guard shape — and both are bounded by a preceding step that produces the recording, which is the honest treatment for a schema this plan has not observed.

**Type consistency.** `Fail($message, $code)`, `Get-PositiveInt($value, $default)`, `Read-Turns($path)` → `@{Turns; Skipped}`, `Format-Block($block, $maxToolResult)`, `Format-Turn($rec, $maxToolResult)`, `Limit-Text($s, $max)`, `New-Header($total, $elided, $skippedLines)`, `Build($truncate)`, `Get-ClaudePath()`, `Write-LogRow($verdict, $envelope, $durationMs, $source)` are each defined once and used with matching arity throughout. `Write-LogRow` is defined in Task 6, above the pre-spawn guard that is its first caller, and reused by Task 7 — one writer, so the twelve fields cannot drift between the guard path and the spawn path. `$charsSent`, `$turnsRendered`, `$elided`, `$skipped` are set in Tasks 4–5 and consumed by the `-DryRun` block in Task 6 and the log row in Task 7 under those exact names, which are also the JSON keys the tests assert on (`chars_sent`, `turns_rendered`, `turns_elided`, `lines_skipped`).
