#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'

    # The parameter is -BridgeHome, NOT -Home. `$HOME` is a PowerShell automatic
    # variable with Options `ReadOnly, AllScope`, and AllScope propagates it into
    # every child scope - so a parameter named `$Home` cannot be bound. Every
    # call would throw "Cannot overwrite variable Home because it is read-only
    # or constant" at parameter binding, before the body ever runs.
    function Invoke-Bridge {
        param([string]$BridgeHome, [string[]]$ExtraArgs = @())
        # CLAUDE_CONFIG_DIR and CLAUDE_CODE_SESSION_ID are pinned to throwaway
        # values, exactly as in every other cross-process helper in this suite.
        # Leaving them ambient is not merely untidy: from Task 3 onward a run
        # that reaches the locator inherits the AMBIENT session id, and an agent
        # implementing this plan runs the suite from inside a live Claude Code
        # session whose own transcript sits at precisely the path the locator
        # globs. `-DryRun` would then print the developer's real transcript -
        # absolute paths, email, machine details - to stdout, in a repo whose
        # Global Constraints forbid a real transcript ever being captured. It
        # would also make these assertions depend on that transcript's contents.
        $extra = $ExtraArgs -join ' '
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$BridgeHome'
            `$env:CLAUDE_CODE_SESSION_ID = 'no-such-session'
            & '$script:Script' -ClaudeHome '$BridgeHome' $extra
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
    }

    # $json is deliberately UNTYPED. A `[string]` parameter coerces `$null` to
    # `''`, so `if ($null -ne $json)` would always be true and the "config file
    # is absent" case below would silently become "config file is empty" - a
    # different branch of the script, and not the one the spec names.
    function New-Home($json) {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        if ($null -ne $json) { Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value $json }
        return $h
    }
}

Describe 'enabled gate' {
    It 'exits 1 when enabled is false' {
        $h = New-Home '{"enabled": false}'
        (Invoke-Bridge -BridgeHome $h).Code | Should -Be 1
    }
    It 'exits 1 when the config file is absent' {
        $h = New-Home $null
        (Join-Path $h 'advisor-bridge.json') | Should -Not -Exist   # the case really is "absent"
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'advisor-bridge'
    }
    It 'exits 1 when the config file is not valid JSON' {
        $h = New-Home '{ this is not json'
        (Invoke-Bridge -BridgeHome $h).Code | Should -Be 1
    }
    It 'writes no log row on an exit-1 path' {
        $h = New-Home '{"enabled": false}'
        Invoke-Bridge -BridgeHome $h | Out-Null
        Join-Path $h 'advisor-bridge.log.jsonl' | Should -Not -Exist
    }
}

Describe 'numeric coercion' {
    It 'falls back to the default on a non-numeric charBudget' {
        $h = New-Home '{"enabled": true, "charBudget": "wide"}'
        $r = Invoke-Bridge -BridgeHome $h -ExtraArgs @('-DryRun')
        $r.Text | Should -Not -Match 'wide'
        # No exit-code assertion. At this task the script ends after the config
        # read, so it exits 0; asserting non-zero would fail here and start
        # passing only as a side effect of later tasks appending code. The
        # negative below is the real check - `-as [int]` must not throw under
        # $ErrorActionPreference = 'Stop'.
        $r.Text | Should -Not -Match 'Cannot convert'
    }
    It 'falls back to the default on a negative timeoutSec' {
        $h = New-Home '{"enabled": true, "timeoutSec": -5}'
        $r = Invoke-Bridge -BridgeHome $h -ExtraArgs @('-DryRun')
        $r.Text | Should -Not -Match 'Cannot convert'
    }
}
