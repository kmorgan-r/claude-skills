#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

# install.ps1 against a throwaway -ClaudeHome seeded with a settings.json shaped
# like a real one: another package's SessionStart entry, unrelated categories,
# and top-level keys that must survive the rewrite.

BeforeAll {
    $script:Install = Join-Path $PSScriptRoot '..' 'install.ps1'
    $script:Package = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

    $script:SiblingSettings = @'
{
  "model": "opus[1m]",
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "python ~/.claude/hooks/advisor-bridge-status.py", "timeout": 10 } ] } ],
    "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "python ~/.claude/hooks/other-guard.py", "timeout": 5 } ] } ],
    "PostToolUse": [ { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/keep-awake.sh touch", "timeout": 10 } ] } ]
  }
}
'@

    $script:Homes = [System.Collections.Generic.List[string]]::new()

    function New-FakeClaudeHome([string]$settings) {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ow-inst-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        $script:Homes.Add($h)
        if ($settings) { $settings | Set-Content -LiteralPath (Join-Path $h 'settings.json') }
        $h
    }

    function Get-Commands($h, [string]$event) {
        $j = Get-Content -Raw -LiteralPath (Join-Path $h 'settings.json') | ConvertFrom-Json -AsHashtable
        @(@($j.hooks[$event]) | ForEach-Object { $_.hooks } | ForEach-Object { $_.command } | Where-Object { $_ })
    }
}

AfterAll {
    foreach ($h in $script:Homes) {
        # A junction left behind would make Remove-Item -Recurse follow it into the package.
        $j = Join-Path $h 'skills' 'ollama-workers'
        if (Test-Path -LiteralPath $j) { cmd /c rmdir $j | Out-Null }
        Remove-Item -Recurse -Force -LiteralPath $h -ErrorAction SilentlyContinue
    }
}

Describe 'installer' {
    It 'registers the route gate as a PreToolUse hook on Agent' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $LASTEXITCODE | Should -Be 0
        $j = Get-Content -Raw -LiteralPath (Join-Path $h 'settings.json') | ConvertFrom-Json -AsHashtable
        $gate = @($j.hooks.PreToolUse) | Where-Object { @($_.hooks).command -like '*ollama-workers-route-gate*' }
        @($gate).Count | Should -Be 1
        $gate.matcher | Should -Be 'Agent'
    }

    It 'copies the route gate hook' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        Join-Path $h 'hooks' 'ollama-workers-route-gate.py' | Should -Exist
    }

    It 'keeps every pre-existing hook entry and top-level key' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        Get-Commands $h 'SessionStart' | Should -Contain 'python ~/.claude/hooks/advisor-bridge-status.py'
        Get-Commands $h 'SessionStart' | Should -Contain 'python ~/.claude/hooks/ollama-workers-status.py'
        Get-Commands $h 'PreToolUse'   | Should -Contain 'python ~/.claude/hooks/other-guard.py'
        Get-Commands $h 'PostToolUse'  | Should -Be @('bash ~/.claude/hooks/keep-awake.sh touch')
        (Get-Content -Raw -LiteralPath (Join-Path $h 'settings.json') | ConvertFrom-Json).model | Should -Be 'opus[1m]'
    }

    It 'is idempotent - a second run adds no duplicate entry' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        @(Get-Commands $h 'PreToolUse' | Where-Object { $_ -like '*ollama-workers-route-gate*' }).Count | Should -Be 1
        @(Get-Commands $h 'SessionStart' | Where-Object { $_ -like '*ollama-workers-status*' }).Count | Should -Be 1
    }

    It 'seeds routing enforcement off' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $s = Get-Content -Raw -LiteralPath (Join-Path $h 'ollama-workers.json') | ConvertFrom-Json
        # Exact $false, not merely falsy: a missing key would also be falsy.
        $s.PSObject.Properties.Name | Should -Contain 'enforceRouting'
        $s.enforceRouting | Should -Be $false
        $s.enabled | Should -Be $false
    }

    It 'does not fail when the skills directory is a junction to this package' {
        # The author's own setup: ~/.claude/skills/ollama-workers is a junction to
        # the checkout, so copying SKILL.md is a copy onto itself, which
        # Copy-Item refuses with "Cannot overwrite the item ... with itself".
        $h = New-FakeClaudeHome $script:SiblingSettings
        New-Item -ItemType Directory -Path (Join-Path $h 'skills') -Force | Out-Null
        cmd /c mklink /J (Join-Path $h 'skills' 'ollama-workers') $script:Package | Out-Null
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        Join-Path $h 'hooks' 'ollama-workers-route-gate.py' | Should -Exist
        cmd /c rmdir (Join-Path $h 'skills' 'ollama-workers') | Out-Null
    }

    It 'dry-run writes nothing' {
        $h = New-FakeClaudeHome $script:SiblingSettings
        $before = Get-Content -Raw -LiteralPath (Join-Path $h 'settings.json')
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h -DryRun | Out-Null
        Get-Content -Raw -LiteralPath (Join-Path $h 'settings.json') | Should -Be $before
        Join-Path $h 'hooks' | Should -Not -Exist
    }

    It 'survives a settings.json with no hooks key at all' {
        $h = New-FakeClaudeHome '{ "model": "opus[1m]" }'
        & pwsh -NoProfile -File $script:Install -ClaudeHome $h | Out-Null
        $LASTEXITCODE | Should -Be 0
        Get-Commands $h 'PreToolUse' | Should -Contain 'python ~/.claude/hooks/ollama-workers-route-gate.py'
        Get-Commands $h 'SessionStart' | Should -Contain 'python ~/.claude/hooks/ollama-workers-status.py'
    }
}
