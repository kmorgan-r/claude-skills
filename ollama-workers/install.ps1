#Requires -Version 7
<#
.SYNOPSIS
Installs the ollama-workers skill and wires up the pieces a plain `cp -r`
cannot: the forwarder agent, the wrapper script, and the SessionStart hook.

.DESCRIPTION
Idempotent. Existing state and settings overlays are left alone. settings.json
is re-serialized to add one SessionStart hook entry, after a timestamped
backup: hook entries and other settings are preserved, but formatting is
normalised. The rewrite is verified before it is kept and rolled back to the
backup if anything is lost. Run with -DryRun to see the plan without touching
anything.
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

    # A generic list, not a PowerShell array. The trap here is the pipeline, not
    # ConvertTo-Json: `$x | ForEach-Object {...}` yields a bare object when $x
    # has one element, and that object then serialises as a JSON object where
    # Claude Code needs an array. Arrays that come straight from
    # ConvertFrom-Json keep their type and round-trip correctly at any length,
    # so untouched hook categories are safe; only what we rebuild needs care.
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
            # -Depth 100 is the maximum. Past the limit ConvertTo-Json renders
            # nested objects as their type name and only emits a warning, which
            # $ErrorActionPreference does not catch - hence the check below.
            $json | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $settings -Encoding utf8

            # Rewriting the whole file to add one entry is only acceptable if
            # the rewrite is checked. Compare every key against the backup,
            # value by value, and put the backup back if anything moved.
            $before = Get-Content -Raw -LiteralPath $backup | ConvertFrom-Json -AsHashtable
            $lost = [System.Collections.Generic.List[string]]::new()
            try {
                $after = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
            }
            catch { $after = $null; $lost.Add('file no longer parses as JSON') }

            if ($after) {
                foreach ($key in $before.Keys) {
                    if (-not $after.ContainsKey($key)) { $lost.Add("dropped '$key'"); continue }
                    if ($key -eq 'hooks') { continue }
                    $b = $before[$key] | ConvertTo-Json -Depth 100 -Compress
                    $a = $after[$key]  | ConvertTo-Json -Depth 100 -Compress
                    if ($b -ne $a) { $lost.Add("changed '$key'") }
                }
                foreach ($cat in @($before.hooks.Keys)) {
                    $b = $before.hooks[$cat] | ConvertTo-Json -Depth 100 -Compress
                    $a = $after.hooks[$cat]  | ConvertTo-Json -Depth 100 -Compress
                    # SessionStart is the one we appended to, so it must differ.
                    if ($cat -eq 'SessionStart') { continue }
                    if ($b -ne $a) { $lost.Add("changed hook '$cat'") }
                }
                $written = @($after.hooks.SessionStart) | ForEach-Object { $_.hooks } |
                    Where-Object { $_.command -like '*ollama-workers-status*' }
                if (-not $written) { $lost.Add('SessionStart entry was not written') }
            }

            if ($lost.Count) {
                Copy-Item -LiteralPath $backup -Destination $settings -Force
                throw "settings.json rewrite lost data ($($lost -join '; ')) - restored from $backup, nothing changed"
            }
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
