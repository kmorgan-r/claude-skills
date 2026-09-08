#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'
    # Dot-source just the pure functions by running the script with a switch that
    # stops before any I/O. -DryRun prints JSON including the render, which is
    # what these assert against.
    function Render-Fixture {
        param([string]$Fixture, [hashtable]$Config = @{})
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        $cfg = @{ enabled = $true } + $Config
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value ($cfg | ConvertTo-Json)
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures $Fixture) (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun" 2>&1
        ($out -join "`n") | ConvertFrom-Json
    }
}

Describe 'record filters' {
    It 'drops attachment records and keeps user and assistant' {
        $r = Render-Fixture 'basic.jsonl'
        $r.render | Should -Not -Match 'ATTACHMENT-MARKER'
        $r.turns_rendered | Should -Be 2
    }
    It 'drops isSidechain true, keeps false, and keeps the key absent entirely' {
        $r = Render-Fixture 'sidechain.jsonl'
        $r.turns_rendered | Should -Be 2
        $r.render | Should -Not -Match 'SIDECHAIN-MARKER'
    }
    It 'reports unparseable lines rather than dying on them' {
        $r = Render-Fixture 'truncated.jsonl'
        $r.lines_skipped | Should -Be 1
        $r.turns_rendered | Should -Be 2
    }
    # message.content can be a bare STRING (an ordinary typed user message),
    # not only a block array. long.jsonl's first turn is exactly that shape.
    # Wrapping a string in @() yields a one-element array whose element has no
    # .type, so a renderer missing the -is [string] branch in Format-Turn
    # would fall through Format-Block's `default` case and render an empty
    # body - this asserts the marker survives, which only happens if the
    # string branch actually ran.
    It 'renders a bare-string message.content rather than an empty body' {
        $r = Render-Fixture 'long.jsonl'
        $r.render | Should -Match 'FIRST-MESSAGE-MARKER-END'
    }
}

Describe 'block caps' {
    It 'caps thinking at 600 chars' {
        $r = Render-Fixture 'caps.jsonl'
        $r.render | Should -Match '\[thinking\]'
        ([regex]::Matches($r.render, '\[thinking\] (.{0,700}?)(\r?\n|$)') |
            ForEach-Object { $_.Groups[1].Value.Length } |
            Measure-Object -Maximum).Maximum | Should -BeLessOrEqual 620
        # 620, not 600: Limit-Text appends ' [truncated]' (12 chars) AFTER the
        # 600-char cut, so a capped line is 612 long. Asserting 600 fails on
        # correct output.
    }
    It 'caps tool_use input at 800 chars' {
        $r = Render-Fixture 'caps.jsonl'
        $r.render | Should -Match '\[tool_use\]'
        ($r.render -split "`n" | Where-Object { $_ -match '\[tool_use\]' } |
            ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum |
            Should -BeLessOrEqual 900   # 800 + the name prefix
    }
    It 'caps tool_result mid-transcript AND inside the last 12 turns' {
        $r = Render-Fixture 'caps.jsonl' -Config @{ maxToolResultChars = 100 }
        $r.render | Should -Match '\[tool_result\]'
        ($r.render -split "`n" | Where-Object { $_ -match '\[tool_result\]' } |
            ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum |
            Should -BeLessOrEqual 130
    }
}

Describe 'header' {
    It 'names cwd, branch, model, turn count, elided and skipped' {
        $r = Render-Fixture 'basic.jsonl'
        $r.render | Should -Match 'cwd:'
        $r.render | Should -Match 'branch:'
        $r.render | Should -Match 'caller model:'
        $r.render | Should -Match 'turns:'
        $r.render | Should -Match 'elided:'
        $r.render | Should -Match 'unparseable lines skipped:'
    }
}

Describe 'non-empty check' {
    It 'exits 1 naming the transcript when nothing survives the filters' {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures 'empty.jsonl') (Join-Path $proj 'fix-session.jsonl')
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 1
        ($out -join "`n") | Should -Match 'fix-session.jsonl'
        # Paired per Rule 1: the filename alone could stay green if a later
        # fail-closed step (e.g. Task 5's budget check) also names
        # $transcriptPath in its own message and this check were deleted.
        # 'no user or assistant turns survived' is unique to this Fail call.
        ($out -join "`n") | Should -Match 'no user or assistant turns survived'
    }
}
