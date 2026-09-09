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

# Validate the package BEFORE the dry-run short-circuit. The sibling's
# install.ps1 runs this same check unconditionally, outside the `if (-not
# $DryRun)` that wraps only the copy itself, and a dry run whose whole job is
# "tell me what would happen" must not be the one mode that cannot say
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
            # $ErrorActionPreference = 'Stop'. The sibling's install.ps1 carries
            # the same guard for the same reason.
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
