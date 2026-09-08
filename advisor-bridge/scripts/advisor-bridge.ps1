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
