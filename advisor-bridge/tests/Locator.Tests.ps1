#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'

    function New-Enabled-Home {
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        return $h
    }
    function New-Projects([string]$base, [string[]]$projectDirs, [string]$sessionId) {
        foreach ($d in $projectDirs) {
            $p = Join-Path $base 'projects' $d
            New-Item -ItemType Directory -Path $p -Force | Out-Null
            Set-Content -LiteralPath (Join-Path $p "$sessionId.jsonl") -Value '{"type":"user"}'
        }
    }
    # -BridgeHome, not -Home: `$HOME` is ReadOnly + AllScope, so a parameter of
    # that name cannot be bound and every call would throw at parameter binding.
    # Same reason as Config.Tests.ps1.
    function Invoke-Locate {
        param([string]$BridgeHome, [string]$ConfigDir, [string]$SessionId)
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$ConfigDir'
            `$env:CLAUDE_CODE_SESSION_ID = '$SessionId'
            & '$script:Script' -ClaudeHome '$BridgeHome' -DryRun
            exit `$LASTEXITCODE" 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
    }
}

Describe 'session locator' {
    It 'exits 1 with a remedy when the session id is unset' {
        $h = New-Enabled-Home
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId ''
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'CLAUDE_CODE_SESSION_ID'
    }
    It 'exits 1 naming the search path when nothing matches' {
        $h = New-Enabled-Home
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'no-such-session'
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'no transcript'
    }
    It 'exits 1 listing every path when more than one matches' {
        $h = New-Enabled-Home
        New-Projects -base $h -projectDirs @('C--a--repo', 'C--b--repo') -sessionId 'dup-id'
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'dup-id'
        $r.Code | Should -Be 1
        $r.Text | Should -Match 'matches 2 transcripts'
        $r.Text | Should -Match 'C--a--repo'
        $r.Text | Should -Match 'C--b--repo'
    }
    It 'never falls back to a nearby transcript' {
        $h = New-Enabled-Home
        New-Projects -base $h -projectDirs @('C--a--repo') -sessionId 'some-other-session'
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'wanted-session'
        $r.Code | Should -Be 1
        $r.Text | Should -Not -Match 'some-other-session'
    }
    # Pins the escape fix: `Get-Item -Path` treats its whole argument as a
    # wildcard pattern, so an unescaped session id of '*' would glob-match any
    # transcript under any project directory - resolving a DIFFERENT session's
    # transcript with exit 0 and no warning. That is the exact failure the
    # rest of this Describe block claims to refuse.
    It 'does not treat a wildcard session id as a glob that matches everything' {
        $h = New-Enabled-Home
        New-Projects -base $h -projectDirs @('C--a--repo') -sessionId 'victims-real-session'
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId '*'
        $r.Code | Should -Be 1
        $r.Text | Should -Not -Match 'victims-real-session'
    }
    # All five tests above assert exit code 1. An error in the RESOLVED-path
    # expression itself - the wrong element of $found picked, .FullName typoed,
    # an escaping bug that happens to still fail to match - would pass every
    # one of them, because none ever exercises the branch that returns a path
    # rather than a "no transcript" or "matches N" failure. This is the
    # positive control: a single real match must resolve at exit 0, and it is
    # pinned by content, not just exit code - a GUID marker unique to this
    # transcript must survive locate-then-render, which only happens if the
    # locator handed the renderer THIS file rather than failing closed or
    # reading some other one.
    It 'resolves a single matching transcript and renders its content at exit 0' {
        $h = New-Enabled-Home
        $marker = 'LOCATOR-HAPPY-PATH-MARKER-' + [guid]::NewGuid().ToString('N')
        $proj = Join-Path $h 'projects' 'C--only-repo'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        $line = (@{ type = 'user'; message = @{ role = 'user'; content = $marker } } |
                 ConvertTo-Json -Compress)
        Set-Content -LiteralPath (Join-Path $proj 'happy-session.jsonl') -Value $line
        $r = Invoke-Locate -BridgeHome $h -ConfigDir $h -SessionId 'happy-session'
        $r.Code | Should -Be 0
        $json = $r.Text | ConvertFrom-Json
        $json.render | Should -Match $marker
    }
}
