#Requires -Version 7
<#
.SYNOPSIS
Installs the ollama-workers skill and wires up the pieces a plain `cp -r`
cannot: the forwarder agent, the wrapper script, and the SessionStart hook.

.DESCRIPTION
Idempotent. Existing state and settings overlays are left alone; the only edit
to settings.json is one SessionStart hook entry, added after a timestamped
backup. Run with -DryRun to see the plan without touching anything.
#>
[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'

$src        = $PSScriptRoot
$claudeHome = Join-Path $HOME '.claude'
$settings   = Join-Path $claudeHome 'settings.json'
$state      = Join-Path $claudeHome 'ollama-workers.json'
$overlay    = Join-Path $claudeHome 'ollama-settings.json'

function Step([string]$message) { Write-Host "  $message" }

$copies = @(
    @{ From = 'SKILL.md';                     To = Join-Path $claudeHome 'skills\ollama-workers\SKILL.md' }
    @{ From = 'agents\ollama-worker.md';      To = Join-Path $claudeHome 'agents\ollama-worker.md' }
    @{ From = 'scripts\ollama-worker.ps1';    To = Join-Path $claudeHome 'scripts\ollama-worker.ps1' }
    @{ From = 'hooks\ollama-workers-status.py'; To = Join-Path $claudeHome 'hooks\ollama-workers-status.py' }
)

# Seeded only when absent: overwriting either of these would discard the
# user's chosen model or an overlay their own launcher already depends on.
$seeds = @(
    @{ From = 'ollama-workers.example.json';  To = $state }
    @{ From = 'ollama-settings.example.json'; To = $overlay }
)

Write-Host "ollama-workers install$(if ($DryRun) { ' (dry run)' })"

Write-Host 'Files:'
foreach ($c in $copies) {
    $from = Join-Path $src $c.From
    if (-not (Test-Path -LiteralPath $from)) { throw "missing from package: $($c.From)" }
    $verb = if (Test-Path -LiteralPath $c.To) { 'overwrite' } else { 'create' }
    Step "$verb $($c.To)"
    if (-not $DryRun) {
        $parent = Split-Path -Parent $c.To
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $from -Destination $c.To -Force
    }
}

Write-Host 'Seeds (kept if they already exist):'
foreach ($s in $seeds) {
    if (Test-Path -LiteralPath $s.To) {
        Step "keep $($s.To)"
    }
    else {
        Step "create $($s.To)"
        if (-not $DryRun) { Copy-Item -LiteralPath (Join-Path $src $s.From) -Destination $s.To }
    }
}

Write-Host 'SessionStart hook:'
$hookCommand = 'python ~/.claude/hooks/ollama-workers-status.py'
if (-not (Test-Path -LiteralPath $settings)) {
    Step "no settings.json at $settings - add this hook yourself: $hookCommand"
}
else {
    $json = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
    $hooks = if ($json.ContainsKey('hooks')) { $json.hooks } else { @{} }

    # A generic list, not a PowerShell array: ConvertTo-Json renders a
    # one-element array as a bare object, and Claude Code needs an array here.
    $sessionStart = [System.Collections.Generic.List[object]]::new()
    if ($hooks.ContainsKey('SessionStart')) {
        foreach ($group in @($hooks.SessionStart)) { $sessionStart.Add($group) }
    }
    $already = $sessionStart | ForEach-Object { $_.hooks } | Where-Object { $_.command -like '*ollama-workers-status*' }

    if ($already) {
        Step 'already registered'
    }
    else {
        Step 'add entry (settings.json backed up first)'
        if (-not $DryRun) {
            $backup = "$settings.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item -LiteralPath $settings -Destination $backup
            $entry = [System.Collections.Generic.List[object]]::new()
            $entry.Add(@{ type = 'command'; command = $hookCommand; timeout = 10 })
            $sessionStart.Add(@{ hooks = $entry })
            $hooks['SessionStart'] = $sessionStart
            $json['hooks'] = $hooks
            $json | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $settings -Encoding utf8
            Step "backup at $backup"
        }
    }
}

Write-Host 'Prerequisites:'
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if ($ollama) { Step "ollama found at $ollama" }
else { Step 'ollama NOT found on PATH - install it and `ollama signin` before enabling workers' }

Write-Host ''
Write-Host 'Done. Enable with /ollama-workers on, check with /ollama-workers status.'
Write-Host 'The forwarder agent registers on the next Claude Code session.'
