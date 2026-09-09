#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll {
    $script:Script   = Join-Path $PSScriptRoot '..' 'scripts' 'advisor-bridge.ps1'
    $script:Fixtures = Join-Path $PSScriptRoot 'fixtures'

    # One list, asserted on both the success row and the timeout row. A row that
    # silently lost half its columns when the call failed would be worst exactly
    # where it is most needed.
    $script:LogFields = @('ts','session_id','model','chars_sent','turns_rendered',
                          'turns_elided','lines_skipped','input_tokens','output_tokens',
                          'cost_usd','duration_ms','verdict')

    function New-FixtureHome {
        param([string]$SessionFixture = 'basic.jsonl')
        $h = Join-Path ([System.IO.Path]::GetTempPath()) ("ab-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $h -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true}'
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge-persona.md') -Value 'be terse'
        $proj = Join-Path $h 'projects' 'C--fixture'
        New-Item -ItemType Directory -Path $proj -Force | Out-Null
        Copy-Item (Join-Path $script:Fixtures $SessionFixture) (Join-Path $proj 'fix-session.jsonl')
        return $h
    }

    function Invoke-WithEnvelope {
        param([string]$Envelope, [string[]]$Extra = @())
        $h = New-FixtureHome
        $ef = Join-Path $script:Fixtures $Envelope
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -EnvelopeFile '$ef' $($Extra -join ' ')
            exit `$LASTEXITCODE" 2>&1
        $log = Join-Path $h 'advisor-bridge.log.jsonl'
        [pscustomobject]@{
            Code = $LASTEXITCODE
            Text = ($out -join "`n")
            Rows = if (Test-Path $log) { @(Get-Content $log | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() }
            Home = $h
        }
    }
}

Describe 'post-run model guard' {
    It 'exits 2 and prints nothing when the envelope names another model' {
        $r = Invoke-WithEnvelope 'envelope-wrong-model.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'trips on a mixed envelope naming a second, non-haiku model, because membership is not enough' {
        # Retargeted from the brief's literal fable+haiku fixture: Task 7's own
        # live capture found that shape is what a NORMAL, correctly-routed call
        # produces (see envelope-ok.json and the spec's "Captured shape"), so it
        # cannot also be the fixture proving the guard trips. This fixture pairs
        # the configured model with a real, non-haiku Anthropic model instead -
        # the shape a fallback or a retry against a different model produces,
        # which is the actual case "membership is not enough" is for.
        $r = Invoke-WithEnvelope 'envelope-two-models.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'trips when modelUsage is absent entirely rather than passing on a null compare' {
        $r = Invoke-WithEnvelope 'envelope-no-modelusage.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'trips when only the haiku helper appears and the configured model never did' {
        # Catches an implementation that checks "every OTHER key is haiku" but
        # forgets to also require the configured model's own presence - that
        # implementation would see zero non-haiku extras here and pass.
        $r = Invoke-WithEnvelope 'envelope-haiku-only.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'passes the right model through and marks the row as canned' {
        # envelope-ok.json carries the REAL shape observed in Task 7's live
        # capture: the configured model plus a claude-haiku-* housekeeping
        # entry. If the haiku exemption were deleted, this is the test that
        # fails - not a synthetic one-key fixture no real call ever produces.
        $r = Invoke-WithEnvelope 'envelope-ok.json'
        $r.Code | Should -Be 0
        $r.Text | Should -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'ok'
        $r.Rows[-1].source  | Should -Be 'envelope-file'
    }
}

Describe 'the other terminal verdicts' {
    # Without these three, child_error and no_envelope - two of the five
    # documented verdicts - are never reached by any test, and the precedence
    # rule lives only in a comment.
    It 'reports child_error when the envelope says is_error' {
        $r = Invoke-WithEnvelope 'envelope-child-error.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'child_error'
    }
    It 'lets model_guard beat child_error when both apply' {
        $r = Invoke-WithEnvelope 'envelope-error-and-wrong-model.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'reports no_envelope when nothing parses as a result envelope' {
        $r = Invoke-WithEnvelope 'envelope-malformed.json'
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'no_envelope'
    }
}

Describe 'log row' {
    It 'carries all twelve documented fields' {
        $r = Invoke-WithEnvelope 'envelope-ok.json'
        foreach ($f in $script:LogFields) {
            $r.Rows[-1].PSObject.Properties.Name | Should -Contain $f
        }
    }
    It 'records real token and cost figures on a model_guard trip, not nulls' {
        # The call was billed. Nulling the cost here would hide real spend in
        # the one column `## Cost` calibrates from.
        $r = Invoke-WithEnvelope 'envelope-two-models.json'
        $r.Rows[-1].verdict  | Should -Be 'model_guard'
        $r.Rows[-1].cost_usd | Should -Not -BeNullOrEmpty
    }
}

Describe 'turnsRendered and elided count leading non-user turns correctly' {
    # No existing fixture (basic/caps/long/etc.) starts with anything but a
    # user turn, so $firstUserIdx is 0 everywhere else in the suite and this
    # bug is invisible to it. leading-assistant.jsonl is 3 records: an
    # assistant turn with no user turn before it (e.g. an injected compact
    # summary), then a real user/assistant pair.
    It 'subtracts $firstUserIdx from turns_rendered and adds it into the header/log elided count' {
        $h = New-FixtureHome -SessionFixture 'leading-assistant.jsonl'
        $out = & pwsh -NoProfile -Command "
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' -DryRun
            exit `$LASTEXITCODE" 2>&1
        $LASTEXITCODE | Should -Be 0
        $plan = ($out -join "`n") | ConvertFrom-Json

        # 3 records total; Build() always starts serialization AT $firstUserIdx
        # (1 here), so the leading assistant record never enters $rendered at
        # all - only 2 turns actually appear in the body. The old
        # "$allTurns.Count - $elided" formula reported 3.
        $plan.turns_rendered | Should -Be 2
        # No MIDDLE-turn elision happened (nothing exceeded charBudget); the
        # binary search's own $elided stays 0. This is a distinct axis from
        # $firstUserIdx and must not be conflated with it in $rendered's
        # "[N turns elided]" body marker.
        $plan.turns_elided | Should -Be 1
        $plan.render | Should -Match 'elided: 1'
        $plan.render | Should -Not -Match 'Leading assistant turn'
    }
}

Describe 'timeout' {
    BeforeAll {
        # A real .exe, not a .cmd/.bat: Task 6's Get-ClaudePath refuses a shell
        # shim outright (BatBadBut / CVE-2024-1874 guard - see Env.Tests.ps1
        # "executable resolution") and falls back to it only when a sibling
        # .exe exists next to it, which none does here. A .cmd stub would never
        # reach the spawn path this test exists to exercise: it would exit 1
        # "shell shim" before any timeout logic runs at all. Compiled with
        # csc.exe, which ships with every Windows 10/11 install via the .NET
        # Framework, so the suite stays offline and fetches nothing.
        $script:StubDir = Join-Path ([System.IO.Path]::GetTempPath()) "ab-bin-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $script:StubDir -Force | Out-Null
        $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        if (-not (Test-Path -LiteralPath $csc)) {
            $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
        }
        if (-not (Test-Path -LiteralPath $csc)) {
            throw "csc.exe not found - cannot build the timeout test's stub executable"
        }
        $src = Join-Path $script:StubDir 'stub.cs'
        Set-Content -LiteralPath $src -Value @'
using System.Diagnostics;
class Stub {
    static void Main() {
        // A real grandchild, started after this stub launches - mirroring
        // claude.exe's own node child on Windows, which is the process the
        // orphan check below must prove Kill($true) actually reaches.
        var psi = new ProcessStartInfo("ping", "-n 12 127.0.0.1");
        psi.UseShellExecute = false;
        var p = Process.Start(psi);
        p.WaitForExit();
    }
}
'@
        $script:StubExe = Join-Path $script:StubDir 'claude.exe'
        $compileOut = & $csc /nologo "/out:$script:StubExe" $src 2>&1
        if (-not (Test-Path -LiteralPath $script:StubExe)) {
            throw "failed to compile timeout stub: $($compileOut -join "`n")"
        }
    }

    It 'kills a slow child, exits 2, logs null cost and a real duration' {
        $h = New-FixtureHome

        # Not `& pwsh ... 2>&1`. .NET (the stub included) always starts child
        # processes with bInheritHandles=true, so `ping` inherits a duplicate of
        # this outer pwsh's own stdout pipe along with everything else - the very
        # "holding the pipe" hazard the spawn code's own Kill($true) comment
        # names for claude.exe's node child. `2>&1` capture blocks until that
        # pipe's write end sees EOF, i.e. until every process holding a copy -
        # ping included - closes it; on a regression to a parent-only Kill(),
        # that means blocking for ping's FULL ~11 s lifetime, by which point it
        # has already exited on its own and the orphan check below would find
        # nothing regardless of which Kill() ran. `Start-Process -Wait` does not
        # fix this either: on Windows it waits on a job object covering the
        # process AND ITS DESCENDANTS, so it too would wait out ping's lifetime.
        # A raw ProcessStartInfo with no redirection at all and a bare
        # WaitForExit() waits on the PROCESS HANDLE only, ignoring both pipes and
        # descendants - so it returns as soon as this outer pwsh (P2) itself
        # exits, whether that is at ~2 s (correct Kill($true)) or ~2 s NB. ping
        # still running (regressed Kill()).
        $outerPsi = [System.Diagnostics.ProcessStartInfo]::new('pwsh')
        $outerPsi.UseShellExecute = $false
        $outerPsi.CreateNoWindow  = $true
        foreach ($a in @('-NoProfile', '-File', $script:Script, '-ClaudeHome', $h, '-TimeoutSec', '2')) {
            [void]$outerPsi.ArgumentList.Add($a)
        }
        $outerPsi.Environment.Clear()
        foreach ($k in @('PATH','PATHEXT','COMSPEC','USERPROFILE','HOME','TEMP','SystemRoot','APPDATA','LOCALAPPDATA')) {
            $v = [Environment]::GetEnvironmentVariable($k)
            if ($null -ne $v) { $outerPsi.Environment[$k] = $v }
        }
        $outerPsi.Environment['PATH']                   = "$($script:StubDir);$($outerPsi.Environment['PATH'])"
        $outerPsi.Environment['CLAUDE_CONFIG_DIR']      = $h
        $outerPsi.Environment['CLAUDE_CODE_SESSION_ID'] = 'fix-session'

        $start   = Get-Date   # anchor for the orphan-process check below
        $outer   = [System.Diagnostics.Process]::Start($outerPsi)
        $exited  = $outer.WaitForExit(20000)

        $exited | Should -BeTrue -Because 'the script itself must exit at its own ~2s timeout, independent of whether the killed child left an orphan behind'
        $outer.ExitCode | Should -Be 2
        $row = (Get-Content (Join-Path $h 'advisor-bridge.log.jsonl') | Select-Object -Last 1) | ConvertFrom-Json
        $row.verdict     | Should -Be 'timeout'
        $row.cost_usd    | Should -BeNullOrEmpty
        $row.duration_ms | Should -BeGreaterThan 1500
        $row.duration_ms | Should -BeLessThan 8000

        # The spec requires the twelve fields on the TIMEOUT path too, not only
        # on success - a row that silently lost half its columns when the call
        # failed would be worst exactly where it is most needed.
        foreach ($f in $script:LogFields) {
            $row.PSObject.Properties.Name | Should -Contain $f
        }

        # No orphaned child. Kill($true) takes the whole tree because `claude` on
        # Windows launches a further process; a regression to a parent-only
        # Kill() leaves that grandchild running and holding the pipe, and every
        # assertion above still passes. This is the only check that catches it.
        # The stub's grandchild is `ping`, started after the run began.
        Start-Sleep -Milliseconds 500
        @(Get-Process -Name 'PING' -ErrorAction SilentlyContinue |
            Where-Object { $_.StartTime -gt $start }).Count | Should -Be 0
    }
}
