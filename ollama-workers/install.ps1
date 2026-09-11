#Requires -Version 7
<#
.SYNOPSIS
Installs the ollama-workers skill and wires up the pieces a plain `cp -r`
cannot: the forwarder agent, the wrapper script, the SessionStart hook, and the
PreToolUse routing gate.

.DESCRIPTION
Idempotent. Existing state and settings overlays are left alone, and a file
whose installed copy is already identical is not rewritten. settings.json is
re-serialized to add one SessionStart and one PreToolUse hook entry, after a
timestamped backup: hook entries and other settings are preserved, but
formatting is normalised. The rewrite is verified before it is kept and rolled
back to the backup if anything is lost. Run with -DryRun to see the plan
without touching anything.

The routing gate is installed switched off: the state file is seeded with
enforceRouting false, and an existing state file without the key reads as off.
Claude Code reloads hooks from settings.json in running sessions, so an install
that switched enforcement on would change the behaviour of every open session
at once.
#>
[CmdletBinding()]
param([switch]$DryRun, [string]$ClaudeHome)

$ErrorActionPreference = 'Stop'

$src        = $PSScriptRoot
$claudeHome = if ($ClaudeHome) { $ClaudeHome } else { Join-Path $HOME '.claude' }
$settings   = Join-Path $claudeHome 'settings.json'
$state      = Join-Path $claudeHome 'ollama-workers.json'
$overlay    = Join-Path $claudeHome 'ollama-settings.json'

# One entry per hook this package owns. Like finds an existing registration, so
# a re-run adds nothing.
$registrations = @(
    @{ Event = 'SessionStart'; Matcher = $null;   Command = 'python ~/.claude/hooks/ollama-workers-status.py';     Like = '*ollama-workers-status*' }
    @{ Event = 'PreToolUse';   Matcher = 'Agent'; Command = 'python ~/.claude/hooks/ollama-workers-route-gate.py'; Like = '*ollama-workers-route-gate*' }
)
$ownedEvents = @($registrations | ForEach-Object { $_.Event } | Select-Object -Unique)

function Get-EventCommands($json, [string]$event) {
    if (-not $json -or -not $json.hooks -or -not $json.hooks.ContainsKey($event)) { return @() }
    @(@($json.hooks[$event]) | ForEach-Object { $_.hooks } | ForEach-Object { $_.command } | Where-Object { $_ })
}

# Collected before any write; the verification below asserts each one survived.
$pre = $null
if (Test-Path -LiteralPath $settings) {
    try { $pre = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable }
    catch { throw "settings.json is not valid JSON: $settings" }
}
$existingCommands = @{}
foreach ($e in $ownedEvents) { $existingCommands[$e] = @(Get-EventCommands $pre $e) }

function Step([string]$message) { Write-Host "  $message" }

$copies = @(
    @{ From = 'SKILL.md';                         To = Join-Path $claudeHome 'skills\ollama-workers\SKILL.md' }
    @{ From = 'agents\ollama-worker.md';          To = Join-Path $claudeHome 'agents\ollama-worker.md' }
    @{ From = 'scripts\ollama-worker.ps1';        To = Join-Path $claudeHome 'scripts\ollama-worker.ps1' }
    @{ From = 'hooks\ollama-workers-status.py';     To = Join-Path $claudeHome 'hooks\ollama-workers-status.py' }
    @{ From = 'hooks\ollama-workers-route-gate.py'; To = Join-Path $claudeHome 'hooks\ollama-workers-route-gate.py' }
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
    $exists = Test-Path -LiteralPath $c.To
    # An identical installed copy is left alone. Besides keeping a re-run quiet,
    # this is what lets a junctioned skills directory install at all: when
    # skills\ollama-workers is a junction to this package, SKILL.md's
    # destination IS its source, and Copy-Item refuses to "overwrite the item
    # with itself" - a terminating error under 'Stop' that ended the install
    # before any later file was copied.
    if ($exists -and (Get-FileHash -LiteralPath $from).Hash -eq (Get-FileHash -LiteralPath $c.To).Hash) {
        Step "unchanged $($c.To)"
        continue
    }
    Step "$(if ($exists) { 'overwrite' } else { 'create' }) $($c.To)"
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

Write-Host 'Hooks:'
if (-not (Test-Path -LiteralPath $settings)) {
    foreach ($r in $registrations) {
        Step "no settings.json at $settings - add this $($r.Event) hook yourself$(if ($r.Matcher) { " (matcher $($r.Matcher))" }): $($r.Command)"
    }
}
else {
    $json = Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json -AsHashtable
    $hooks = if ($json.ContainsKey('hooks') -and $json.hooks) { $json.hooks } else { @{} }

    $pending = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($r in $registrations) {
        $have = @(Get-EventCommands $json $r.Event | Where-Object { $_ -like $r.Like })
        if ($have.Count) { Step "$($r.Event): already registered" }
        else {
            Step "$($r.Event): add entry$(if ($r.Matcher) { " (matcher $($r.Matcher))" }) (settings.json backed up first)"
            $pending.Add($r)
        }
    }

    if ($pending.Count -and -not $DryRun) {
        $backup = "$settings.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $settings -Destination $backup

        foreach ($r in $pending) {
            # A generic list, not a PowerShell array. The trap here is the
            # pipeline, not ConvertTo-Json: `$x | ForEach-Object {...}` yields a
            # bare object when $x has one element, and that object then
            # serialises as a JSON object where Claude Code needs an array.
            # Arrays that come straight from ConvertFrom-Json keep their type and
            # round-trip correctly at any length, so untouched hook categories
            # are safe; only what we rebuild needs care.
            $groups = [System.Collections.Generic.List[object]]::new()
            if ($hooks.ContainsKey($r.Event)) {
                foreach ($group in @($hooks[$r.Event])) { $groups.Add($group) }
            }
            $entry = [System.Collections.Generic.List[object]]::new()
            $entry.Add(@{ type = 'command'; command = $r.Command; timeout = 10 })
            $newGroup = [ordered]@{}
            if ($r.Matcher) { $newGroup.matcher = $r.Matcher }
            $newGroup.hooks = $entry
            $groups.Add($newGroup)
            $hooks[$r.Event] = $groups
        }
        $json['hooks'] = $hooks
        # -Depth 100 is the maximum. Past the limit ConvertTo-Json renders
        # nested objects as their type name and only emits a warning, which
        # $ErrorActionPreference does not catch - hence the check below.
        $json | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $settings -Encoding utf8

        # Rewriting the whole file to add entries is only acceptable if the
        # rewrite is checked. Compare every key against the backup, value by
        # value, and put the backup back if anything moved.
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
            # Guarded: a settings.json with no `hooks` key at all is ordinary -
            # a file that only sets `model` is enough. Then $before.hooks is
            # $null, @($null.Keys) yields a one-element array holding $null, and
            # $before.hooks[$null] is a terminating error under
            # $ErrorActionPreference = 'Stop', thrown AFTER the rewrite has
            # landed and BEFORE the rollback below.
            if ($before.hooks) {
                foreach ($cat in @($before.hooks.Keys)) {
                    # Categories we append to must differ; they are checked
                    # entry by entry below instead.
                    if ($ownedEvents -contains $cat) { continue }
                    $b = $before.hooks[$cat] | ConvertTo-Json -Depth 100 -Compress
                    $a = $after.hooks[$cat]  | ConvertTo-Json -Depth 100 -Compress
                    if ($b -ne $a) { $lost.Add("changed hook '$cat'") }
                }
            }
            # Every pre-existing entry must survive, not just our own.
            # advisor-bridge also appends to SessionStart, and whichever
            # installer runs second is the one that can destroy the other's
            # entry. Checking only for our own string would verify clean on a
            # file that lost theirs.
            foreach ($e in $ownedEvents) {
                $afterCommands = @(Get-EventCommands $after $e)
                foreach ($cmd in $existingCommands[$e]) {
                    if ($afterCommands -notcontains $cmd) { $lost.Add("dropped $e entry '$cmd'") }
                }
            }
            foreach ($r in $registrations) {
                if (-not @(Get-EventCommands $after $r.Event | Where-Object { $_ -like $r.Like }).Count) {
                    $lost.Add("$($r.Event) entry was not written")
                }
            }
        }

        if ($lost.Count) {
            Copy-Item -LiteralPath $backup -Destination $settings -Force
            throw "settings.json rewrite lost data ($($lost -join '; ')) - restored from $backup, nothing changed"
        }
        Step "backup at $backup"
    }
}

Write-Host 'Prerequisites:'
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if ($ollama) { Step "ollama found at $ollama" }
else { Step 'ollama NOT found on PATH - install it and `ollama signin` before enabling workers' }

Write-Host ''
Write-Host 'Done. Enable with /ollama-workers on, check with /ollama-workers status.'
Write-Host 'Routing enforcement is off until /ollama-workers enforce on.'
Write-Host 'The forwarder agent registers on the next Claude Code session.'
