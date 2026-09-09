#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'

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
    # A bare exit-code assertion never stands alone in this file: every task
    # from Task 3 onward appends another fail-closed step below the gate, and
    # each one exits 1 for its OWN reason - so an exit-1 assertion with no
    # distinctive text passes just as well when the gate itself is deleted
    # and the run instead dies one step later, at the locator, because the
    # helper's session id ('no-such-session') never resolves a transcript.
    # A reviewer proved this by mutation: with the gate's `Fail` removed
    # entirely, all eight tests below still passed. Every exit-1 assertion
    # in this Describe block is therefore paired with
    # `Should -Match 'disabled or unreadable config'` - the text unique to
    # the gate's own `Fail` call - so a test can only pass when the gate
    # itself is what fired.
    It 'exits 1 when enabled is false' {
        $h = New-Home '{"enabled": false}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'exits 1 when the config file is absent' {
        $h = New-Home $null
        (Join-Path $h 'advisor-bridge.json') | Should -Not -Exist   # the case really is "absent"
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'exits 1 when the config file is not valid JSON' {
        $h = New-Home '{ this is not json'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'writes no log row on an exit-1 path' {
        $h = New-Home '{"enabled": false}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Text | Should -Match 'disabled or unreadable config'
        Join-Path $h 'advisor-bridge.log.jsonl' | Should -Not -Exist
    }
    # `-ne $true` array-filters instead of comparing when the left operand is
    # an array, so `{"enabled": []}` and `{"enabled": [true,false]}` would
    # fail OPEN under the plan's original expression - the empty/collection
    # result coerces to false in `if`, and the gate never fires. These four
    # pin the fix (`-isnot [bool] -or -not $cfgRaw.enabled`) against every
    # non-boolean shape the reviewer found, including the deliberate
    # tightening that also closes string/numeric truthy values.
    It 'exits 1 when enabled is an empty array' {
        $h = New-Home '{"enabled": []}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'exits 1 when enabled is a non-empty array' {
        $h = New-Home '{"enabled": [true, false]}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'exits 1 when enabled is the string "true"' {
        $h = New-Home '{"enabled": "true"}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
    It 'exits 1 when enabled is the number 1' {
        $h = New-Home '{"enabled": 1}'
        $r = Invoke-Bridge -BridgeHome $h
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'disabled or unreadable config'
    }
}

Describe 'enabled gate lets a valid config through' {
    # Every numeric test above already uses `enabled: true`, but each asserts
    # only a NEGATIVE text match (`Should -Not -Match 'wide'` /
    # `'Cannot convert'`). A gate that is falsely CLOSED - one that fails
    # every config, valid or not - satisfies both of those negatives just as
    # well as a correctly-open gate: the refusal text
    # ("disabled or unreadable config") matches neither pattern. Nothing above
    # this Describe block would fail if `-not $cfgRaw.enabled` were replaced
    # with a tautology like `$true`. This test is the positive control: a
    # genuinely valid `enabled: true` config, with a persona and a real
    # transcript in place, must reach `-DryRun` output at exit 0 - which is
    # the first point in the whole pipeline where success becomes observable
    # on stdout at all.
    It 'reaches -DryRun output at exit 0 on a valid enabled config' {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 0
        $json = ($out -join "`n") | ConvertFrom-Json
        $json.turns_rendered | Should -Be 2
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
