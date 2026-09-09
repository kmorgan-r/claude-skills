#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

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
        # Direction 1 of the sibling-survival check: advisor-bridge's installer
        # runs onto a home that already carries the OTHER package's entry.
        # This exercises advisor-bridge's own verifier. A verifier written like
        # the sibling's pre-back-fill one - checking only for its own command
        # string - would miss this package's rewrite dropping the ollama-workers
        # entry, and this test would stay green while the sibling's hook silently
        # stopped firing.
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
    It 'is idempotent - a second run adds no duplicate entry' {
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
        ($out -join "`n") | Should -Match "changed hook 'Deep'"
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

Describe 'ollama-workers back-fill' {
    It 'refuses to drop an advisor-bridge SessionStart entry' {
        # Direction 2 of the sibling-survival check: the OTHER installer runs
        # onto a home that already carries advisor-bridge's entry. Before this
        # back-fill, ollama-workers/install.ps1's verifier checked only for its
        # own command string ('*ollama-workers-status*') and skipped the whole
        # SessionStart category in the key-by-key comparison - a rewrite that
        # dropped this entry would have verified clean and kept the damaged
        # file. This test is the regression guard for that exact defect.
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
