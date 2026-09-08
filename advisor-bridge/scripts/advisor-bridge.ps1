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

# --- -DryRun -----------------------------------------------------------
# A deliverable seam, not a test-only afterthought, and it belongs in THIS task:
# every assertion in Render.Tests.ps1 reads this JSON. It is deliberately
# outside the stdout/exit contract - it prints JSON and exits 0 without
# spawning, which is not "advice returned" in the sense of the exit table.
#
# Task 6 moves this block below the environment build and adds env, args, cwd
# and exe.
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
