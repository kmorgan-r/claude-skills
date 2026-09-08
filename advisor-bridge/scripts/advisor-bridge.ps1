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
