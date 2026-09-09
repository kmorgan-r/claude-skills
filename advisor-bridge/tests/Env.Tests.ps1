#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'

    function Invoke-DryRun {
        param([hashtable]$PoisonEnv = @{}, [int]$PersonaChars = 20, [string[]]$Extra = @())
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value ('x' * $PersonaChars)
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $sets = ($PoisonEnv.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = '$($_.Value)'" }) -join "`n"
        $out = & pwsh -NoProfile -Command "
            $sets
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun $($Extra -join ' ')
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n"); Home = $h }
    }
}

Describe 'environment scrub' {
    It 'leaves the child environment equal to the whitelist exactly' {
        $r = Invoke-DryRun -PoisonEnv @{
            ANTHROPIC_BASE_URL           = 'http://127.0.0.1:11434'
            ANTHROPIC_AUTH_TOKEN         = 'ollama'
            ANTHROPIC_DEFAULT_OPUS_MODEL = 'glm-5.3-flash:cloud'
            CLAUDE_CODE_SUBAGENT_MODEL   = 'glm-5.3-flash:cloud'
        }
        $r.Code | Should -Be 0
        $keys = ($r.Text | ConvertFrom-Json).env.PSObject.Properties.Name | Sort-Object
        $expected = @('APPDATA','CLAUDE_EFFORT','COMSPEC','HOME','LOCALAPPDATA',
                      'PATH','PATHEXT','SystemRoot','TEMP','USERPROFILE') | Sort-Object
        ($keys -join ',') | Should -Be ($expected -join ',')
    }
    It 'lets no CLAUDE_CODE_SUBAGENT_MODEL through, which a prefix check would miss' {
        $r = Invoke-DryRun -PoisonEnv @{ CLAUDE_CODE_SUBAGENT_MODEL = 'glm-5.3-flash:cloud' }
        ($r.Text | ConvertFrom-Json).env.PSObject.Properties.Name |
            Should -Not -Contain 'CLAUDE_CODE_SUBAGENT_MODEL'
    }
    It 'sets CLAUDE_EFFORT explicitly rather than inheriting it' {
        $r = Invoke-DryRun -PoisonEnv @{ CLAUDE_EFFORT = 'low' }
        ($r.Text | ConvertFrom-Json).env.CLAUDE_EFFORT | Should -Be 'xhigh'
    }
}

Describe 'pre-spawn guard' {
    # The negative test the guard would otherwise lack. Without it, deleting the
    # whole comparison block leaves every test in this file green - the other
    # three only prove the CONSTRUCTION is right, and construction and guard are
    # derived from the same whitelist, so no poisoned input can separate them.
    It 'exits 2 and logs model_guard when the child environment gains a stray key' {
        $r = Invoke-DryRun -Extra @('-InjectEnvKey', 'STRAY_KEY')
        $r.Code | Should -Be 2
        $r.Text | Should -Match 'does not match the whitelist'
        $r.Text | Should -Match 'STRAY_KEY'
        $row = (Get-Content (Join-Path $r.Home 'advisor-bridge.log.jsonl') |
                Select-Object -Last 1) | ConvertFrom-Json
        $row.verdict | Should -Be 'model_guard'
        # Write-LogRow is shared with Task 7's spawn path specifically so the
        # twelve fields cannot drift between the two callers. That invariant
        # is otherwise unpinned by anything in this suite - a field renamed,
        # dropped, or added on one call site and not the other would pass
        # every assertion above, which checks only `verdict`. `source` is
        # excluded here deliberately: this call passes $source = $null, and
        # Write-LogRow only adds that key when $source is truthy.
        $fields = $row.PSObject.Properties.Name | Sort-Object
        $expectedFields = @('chars_sent','cost_usd','duration_ms','input_tokens',
                            'lines_skipped','model','output_tokens','session_id',
                            'ts','turns_elided','turns_rendered','verdict') | Sort-Object
        ($fields -join ',') | Should -Be ($expectedFields -join ',')
    }
    It 'refuses the injection seam outside a dry run' {
        # Exit 1, and no log row: the seam is rejected before anything is
        # attempted, so it is a wrapper refusal, not an untrustworthy result.
        # timeoutSec: 5, not the 240s production default - the same belt
        # Guard.Tests.ps1's New-FixtureHome carries: if a refactor ever moved
        # -InjectEnvKey handling inside the -DryRun block so it were ignored
        # rather than refused, this bounds the resulting real spawn to 5s
        # instead of 240s before the assertions below fail loudly.
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true, "timeoutSec": 5}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -InjectEnvKey 'STRAY_KEY'
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 1
        ($out -join "`n") | Should -Match 'requires -DryRun'
        # Pins the comment above, not just the exit code: an implementation
        # that moved this check past Write-LogRow's definition point and
        # called it before Fail-ing would still exit 1 with the same text.
        Join-Path $h 'advisor-bridge.log.jsonl' | Should -Not -Exist
    }
}

Describe 'executable resolution' {
    # BatBadBut (CVE-2024-1874): with UseShellExecute = $false, a .cmd/.bat
    # FileName hands the command line to cmd.exe /c, which RE-PARSES it -
    # voiding ArgumentList's per-element CRT quoting, the exact guarantee the
    # 'argument list' Describe block below depends on. `npm install -g`
    # commonly puts such a shim ahead of any real .exe on PATH, so this is
    # fabricated the same way it was proven: a bare claude.cmd placed first
    # on PATH, with no sibling claude.exe next to it.
    It 'refuses a .cmd shim instead of spawning it' {
        $shimDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-shim-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $shimDir -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $shimDir 'claude.cmd') -Value '@echo off'
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'basic.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:PATH = '$shimDir;' + `$env:PATH
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 1
        # Paired per Rule 1: persona-too-large and the injection-seam refusal
        # also exit 1 in this same file. 'shell shim' is unique to this
        # specific Fail call.
        ($out -join "`n") | Should -Match 'shell shim'
    }
}

Describe 'persona preflight' {
    It 'exits 1 naming the limit when the persona is too large' {
        $r = Invoke-DryRun -PersonaChars 20000
        $r.Code | Should -Be 1
        $r.Text | Should -Match '16000'
    }
}

Describe 'argument list' {
    It 'passes the persona as one argument and never builds a command string' {
        $r = Invoke-DryRun
        $a = ($r.Text | ConvertFrom-Json).args
        $a | Should -Contain '--system-prompt'
        $a | Should -Contain '-p'
        $a | Should -Contain '--strict-mcp-config'
        ($a -join ' ') | Should -Match '--setting-sources'
    }
}
