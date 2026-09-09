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
#
# -isnot [bool], not -ne $true: when $cfgRaw.enabled is an array, `-ne`
# array-filters instead of comparing, returning a (possibly empty) collection
# that `if` then coerces to false - so `{"enabled": []}` or
# `{"enabled": [true,false]}` would fail OPEN and spend money. `-isnot [bool]`
# does not array-filter: `@() -isnot [bool]` is `$true`, so the gate fires.
# This also closes `"enabled": "true"` and `"enabled": 1`, which is a
# deliberate tightening, not a regression - every real config path
# (advisor-bridge.example.json, and the `on` command writing through
# ConvertTo-Json) produces a genuine JSON boolean.
if ($null -eq $cfgRaw -or $cfgRaw.enabled -isnot [bool] -or -not $cfgRaw.enabled) {
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

# --- 5. Locate the caller's transcript -------------------------------------
# Glob, rather than recomputing Claude Code's cwd-to-directory-name mangling
# (C:\Users\<you>\... -> C--Users-<you>-...). That rule is undocumented, and
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
# `Get-Item -Path` treats its whole argument as a wildcard pattern, not a
# literal path - so an unescaped session id or base directory containing
# `*`, `?` or `[` would silently glob-match (resolving a DIFFERENT session's
# transcript with exit 0) or silently miss (a legitimate transcript under a
# bracketed directory name). WildcardPattern.Escape neutralises metacharacters
# in the two interpolated components while leaving the literal `*` directory
# segment as a real wildcard.
$wc         = [System.Management.Automation.WildcardPattern]
$pattern    = Join-Path $wc::Escape($callerBase) 'projects' '*' "$($wc::Escape($sessionId)).jsonl"
$found      = @(Get-Item -Path $pattern -ErrorAction SilentlyContinue)

if ($found.Count -eq 0) {
    Fail "no transcript for session $sessionId under $(Join-Path $callerBase 'projects')\*\`n  The session may not have been written yet; send one message and retry."
}
if ($found.Count -gt 1) {
    $list = ($found | ForEach-Object { "    $($_.FullName)" }) -join "`n"
    Fail "session id $sessionId matches $($found.Count) transcripts:`n$list`n  Rendering the wrong one would advise on someone else's session. Delete or move`n  the stale copy, or set CLAUDE_CONFIG_DIR to disambiguate."
}
$transcriptPath = $found[0].FullName

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
            # -eq $true, not -ne $false: a record that omits the field
            # entirely is a main-agent record and must be kept. (-ne $true
            # would also array-filter instead of comparing if isSidechain
            # were ever array-valued - the same hazard that made the enabled
            # gate in Task 2 fail OPEN on `{"enabled": []}` - so do not
            # "simplify" this back to -ne.)
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

# Computed once, not inside New-Header: the budget block below can call
# New-Header (via Build/Join-Render) dozens of times while searching for a
# fitting elision count, and `git rev-parse` is a ~90ms process spawn - at
# hundreds of Build calls that alone dominates render latency in front of a
# paid interactive call. New-Header reads this script-scope variable instead
# of shelling out itself; its own parameter list and output are unchanged.
$gitBranch = try { (& git rev-parse --abbrev-ref HEAD 2>$null) } catch { $null }
if (-not $gitBranch) { $gitBranch = '(not a git repo)' }

function New-Header([int]$total, [int]$elided, [int]$skippedLines) {
    @(
        "cwd: $((Get-Location).Path)"
        "branch: $gitBranch"
        "caller model: $($env:ANTHROPIC_DEFAULT_OPUS_MODEL ?? '(unknown)')"
        "turns: $total"
        "elided: $elided"
        "unparseable lines skipped: $skippedLines"
        ''
    ) -join "`n"
}

# --- Budget ----------------------------------------------------------------
# Six steps. 1 and 2 are preservation floors, not reductions; only 3 through 6
# remove text, and they run in ascending order of what it costs to lose the
# content - which is why the first user message is cut LAST rather than first.
# Steps 3-6 run ONLY "until under charBudget" (spec 2026-09-08, budget
# section): a transcript that already fits must not lose a single turn, so
# each step re-checks the length and stops the moment it is satisfied rather
# than always running to completion.
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

# Step 1 + 2: the floors - the first user message and the last-12 tail window
# are never elided by step 3, no matter how far over budget the transcript is.
# Everything with an index strictly between $firstUserIdx and $tailStart is a
# middle turn: a step-3 elision candidate, oldest (lowest index) first.
$tailStart = [Math]::Max($firstUserIdx + 1, $allTurns.Count - $TAIL)
$midCount  = [Math]::Max(0, $tailStart - ($firstUserIdx + 1))

# Format-Turn re-serializes tool_use/tool_result JSON on every call; caching by
# index means the step-3 loop below - which rebuilds the render once per turn
# it considers eliding - does not redo that work for turns it has already
# rendered on a prior iteration.
$bodyCache = @{}
function Get-Body([int]$idx) {
    if (-not $bodyCache.ContainsKey($idx)) {
        $bodyCache[$idx] = Format-Turn $allTurns[$idx] $maxToolResultChars
    }
    return $bodyCache[$idx]
}

function Build([hashtable]$truncate, [int]$elidedCount) {
    $bodies = [System.Collections.Generic.List[string]]::new()
    $first  = Get-Body $firstUserIdx
    if ($truncate.ContainsKey($firstUserIdx)) { $first = Limit-Text $first $truncate[$firstUserIdx] }
    $bodies.Add($first)
    if ($elidedCount -gt 0) { $bodies.Add("[$elidedCount turns elided]") }
    # The middle turns NOT yet elided: oldest-first elision means the surviving
    # middle turns are always the newest ones, i.e. those just before $tailStart.
    for ($i = $firstUserIdx + 1 + $elidedCount; $i -lt $tailStart; $i++) {
        $bodies.Add((Get-Body $i))
    }
    for ($i = $tailStart; $i -lt $allTurns.Count; $i++) {
        $body = Get-Body $i
        if ($truncate.ContainsKey($i)) { $body = Limit-Text $body $truncate[$i] }
        $bodies.Add($body)
    }
    return Join-Render $bodies.ToArray() $elidedCount $allTurns.Count $skipped
}

$truncate = @{}

# Step 3: drop middle turns oldest-first, stopping at the MINIMUM elision
# count that fits - a transcript that already fits under $charBudget elides
# nothing at all, and $elided ends at 0. Render length is monotonically
# non-increasing in $elided (each step removes one turn body and, once,
# adds a short marker), so the minimum fitting count can be found by binary
# search over [0, $midCount] instead of a linear scan: a linear scan calls
# Build once per elision considered, each Build re-joining a shrinking body
# list, which is O(midCount) work per call and O(midCount^2) total - at 800
# middle turns that was 82s dominated by ~90ms-per-call git spawns (fixed
# above) plus tens of seconds of pure string-rejoin cost. Binary search cuts
# the call count to ~log2(midCount), and still lands on the exact same
# $elided the linear scan would have found, because it is searching for the
# same leftmost-fitting point in a monotonic sequence, not merely a
# sufficient one.
$elided   = 0
$rendered = Build $truncate $elided
if ($rendered.Length -gt $charBudget -and $midCount -gt 0) {
    $atFloor = Build $truncate $midCount
    if ($atFloor.Length -gt $charBudget) {
        # Even eliding every middle turn doesn't fit - land at the full
        # floor state and fall through to steps 4-6, exactly as a linear
        # scan that never found a fit before reaching $midCount would.
        $elided   = $midCount
        $rendered = $atFloor
    } else {
        $lo = 0
        $hi = $midCount
        while ($lo -lt $hi) {
            # [Math]::Floor, not a bare [int] cast: PowerShell's [int] cast on
            # a double uses round-half-to-even, not truncation - [int]1.5 is
            # 2, not 1. With $lo=1, $hi=2 that makes $mid equal to $hi rather
            # than strictly between $lo and $hi, so a fitting Build($mid)
            # leaves $hi unchanged and the loop never terminates. Floor
            # guarantees $lo -le $mid -lt $hi whenever $lo -lt $hi.
            $mid = [int][Math]::Floor(($lo + $hi) / 2.0)
            if ((Build $truncate $mid).Length -le $charBudget) { $hi = $mid } else { $lo = $mid + 1 }
        }
        $elided   = $lo
        $rendered = Build $truncate $elided
    }
}

# Step 4: truncate the tail window oldest-first, down to the first user message
# plus the most recent turn. Reachable only once step 3 has already elided
# every middle turn ($elided -eq $midCount) and the render is still over
# budget - otherwise step 3 would already have stopped the loop above.
# PowerShell's `..` produces a DESCENDING range when start > end - if the
# transcript is short enough that $tailStart already equals $allTurns.Count
# (a 1-turn transcript, or the first user message itself is the last turn),
# $tailStart..($allTurns.Count - 1) would wrongly yield the first user
# message's own index instead of an empty tail window.
$tailIdx = if ($tailStart -lt $allTurns.Count) { @($tailStart..($allTurns.Count - 1)) } else { @() }
for ($t = 0; $t -lt $tailIdx.Count - 1 -and $rendered.Length -gt $charBudget; $t++) {
    $truncate[$tailIdx[$t]] = 200
    $rendered = Build $truncate $elided
}

# Step 5: truncate the most recent turn itself.
if ($rendered.Length -gt $charBudget -and $tailIdx.Count -gt 0) {
    $last = $tailIdx[-1]
    $over = $rendered.Length - $charBudget
    $cur  = (Get-Body $last).Length
    $truncate[$last] = [Math]::Max(200, $cur - $over - 64)
    $rendered = Build $truncate $elided
}

# Step 6: last resort - truncate the first user message.
if ($rendered.Length -gt $charBudget) {
    $over = $rendered.Length - $charBudget
    $cur  = (Get-Body $firstUserIdx).Length
    $truncate[$firstUserIdx] = [Math]::Max(200, $cur - $over - 64)
    $rendered = Build $truncate $elided
}

# The floor of 200 chars per turn means an absurdly small charBudget cannot be
# met. That is a config error, not a case to support - but it must not ship a
# silent overrun either.
if ($rendered.Length -gt $charBudget) {
    Fail "charBudget $charBudget is too small to render even a minimal transcript ($($rendered.Length) chars)`n  Raise charBudget in $configPath."
}

$turnsRendered = $allTurns.Count - $elided
$charsSent     = $rendered.Length

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
