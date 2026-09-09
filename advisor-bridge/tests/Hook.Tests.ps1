#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Hook = Join-Path $PSScriptRoot '..' 'hooks' 'advisor-bridge-status.py'
    # $Config is UNTYPED: a [string] parameter coerces $null to '', so
    # `if ($null -ne $Config)` would always be true and the "config is absent"
    # case below would silently become "config is empty" - a different branch.
    function Invoke-Hook {
        param($Config, [string]$BaseUrl)
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        if ($null -ne $Config) { Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value $Config }
        $out = '{}' | & pwsh -NoProfile -Command "
            `$env:ADVISOR_BRIDGE_HOME = '$h'
            `$env:ANTHROPIC_BASE_URL = '$BaseUrl'
            `$input | python '$script:Hook'" 2>&1
        ($out -join "`n")
    }
}

Describe 'hook gating' {
    It 'is silent when the config is absent' {
        # Genuinely absent, not empty - see the untyped $Config above.
        Invoke-Hook -Config $null -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent when the config is unreadable' {
        Invoke-Hook -Config '{ not json' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent when disabled, even in an Ollama session' {
        Invoke-Hook -Config '{"enabled": false}' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'is silent in an Anthropic session even when enabled' {
        Invoke-Hook -Config '{"enabled": true}' -BaseUrl 'https://api.anthropic.com' | Should -BeNullOrEmpty
    }
    It 'is silent when ANTHROPIC_BASE_URL is unset' {
        Invoke-Hook -Config '{"enabled": true}' -BaseUrl '' | Should -BeNullOrEmpty
    }
    # `if not cfg.get("enabled")` would pass all six tests above and STILL
    # nudge on a non-strict-boolean `enabled` - the exact shape
    # Config.Tests.ps1 (the PowerShell script's own gate) refuses with four
    # dedicated tests, because `claude -p` there exits 1 rather than running.
    # A hook that steers the model into that dead call is the failure the
    # module docstring names. `is not True` (not truthiness) is what closes
    # this, so it gets its own pin rather than riding on the boolean cases.
    It 'is silent when enabled is not a strict boolean (the number 1)' {
        Invoke-Hook -Config '{"enabled": 1}' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    # Pins the isinstance(cfg, dict) guard: `[].get` and `None.get` both raise
    # AttributeError, which is not caught by the module's `except (OSError,
    # ValueError)` around the json.load call and would otherwise surface as a
    # traceback on stderr - non-silent, and merged into $out here by 2>&1.
    It 'is silent when the config is valid JSON but not an object' {
        Invoke-Hook -Config '[]' -BaseUrl 'http://127.0.0.1:11434' | Should -BeNullOrEmpty
    }
    It 'injects the protocol when enabled and the backend is not Anthropic' {
        $out = Invoke-Hook -Config '{"enabled": true}' -BaseUrl 'http://127.0.0.1:11434'
        # Matched against text unique to PROTOCOL, not the module's filename -
        # a run against a MISSING hook prints a traceback containing
        # "advisor-bridge-status.py", which a bare 'advisor-bridge' pattern
        # would match for the wrong reason.
        $out | Should -Match 'You have an advisor'
        $out | Should -Match 'timeout:\s*300000'
    }
}
