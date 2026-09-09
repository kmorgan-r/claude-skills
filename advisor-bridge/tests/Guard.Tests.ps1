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
        # timeoutSec: 20, not the 240s production default - a short belt on top
        # of the PATH-shadow hard guard above, so that even if the guard were
        # ever bypassed or a future test forgot to check it, a spawn that
        # escapes to the real CLI is bounded to 20s, not 240s, before it fails
        # loudly on its assertions.
        Set-Content -LiteralPath (Join-Path $h 'advisor-bridge.json') -Value '{"enabled": true, "timeoutSec": 20}'
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

    # Compiled once here (script scope, not the 'timeout' Describe's own scope)
    # so both the timeout test and the spawn-path coverage below - which needs
    # the exact same "real .exe, not a .cmd/.bat" stub for the same
    # Get-ClaudePath reason documented in the 'timeout' Describe - can use it
    # without compiling twice.
    $script:StubDir = Join-Path ([System.IO.Path]::GetTempPath()) "ab-bin-$([guid]::NewGuid())"
    New-Item -ItemType Directory -Path $script:StubDir -Force | Out-Null
    $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path -LiteralPath $csc)) {
        $csc = Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
    }
    if (-not (Test-Path -LiteralPath $csc)) {
        throw "csc.exe not found - cannot build the timeout/spawn test stub executable"
    }
    $src = Join-Path $script:StubDir 'stub.cs'
    Set-Content -LiteralPath $src -Value @'
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

class Stub {
    // Mode is read from a "mode.txt" file dropped next to this exe, not an
    // argument or env var: advisor-bridge.ps1 builds the child's own
    // ArgumentList and Environment (the whitelist-then-clear pattern) itself,
    // so the test cannot inject either one through the real spawn path
    // without changing production code. A file beside the exe is invisible to
    // both.
    static int Main() {
        string dir  = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string path = Path.Combine(dir, "mode.txt");
        string mode = File.Exists(path) ? File.ReadAllText(path).Trim() : "hang";

        switch (mode) {
            case "orphan":
                // Start a grandchild holding an inherited duplicate of this
                // process's stdio handles, then exit immediately WITHOUT
                // waiting - mirrors claude.exe exiting while its node child
                // still holds the pipe. Exercises the DRAIN bound, not the
                // WaitForExit(timeoutSeconds) bound "hang" below exercises.
                StartPing(wait: false);
                return 0;

            case "envelope":
                // Drain stdin fully first so the parent's WriteAsync always
                // completes - this mode is about the ENVELOPE PARSE on a real
                // spawn, not the stdin-write race "noreadstdin" exercises.
                Console.In.ReadToEnd();
                Console.Out.Write("{\"type\":\"result\",\"is_error\":false,\"result\":\"SECRET-ADVICE-BODY\",\"modelUsage\":{\"claude-fable-5-1\":{\"outputTokens\":42}}}\n");
                Console.Out.Flush();
                return 0;

            case "nonzero":
                Console.In.ReadToEnd();
                return 1;

            case "noreadstdin":
                // Exit immediately without ever reading stdin, to force the
                // parent's WriteAsync to fault on a broken pipe once a render
                // larger than the OS pipe buffer is already pending. Needs a
                // session fixture whose rendered size clears that buffer -
                // see Guard.Tests.ps1's use of long.jsonl for this mode.
                return 0;

            default: // "hang": the original timeout-test shape - block until
                     // the grandchild itself exits, so WaitForExit(timeoutSeconds)
                     // is what has to time out, not the drain.
                StartPing(wait: true);
                return 0;
        }
    }

    static void StartPing(bool wait) {
        var psi = new ProcessStartInfo("ping", "-n 12 127.0.0.1");
        psi.UseShellExecute = false;
        var p = Process.Start(psi);
        if (wait) { p.WaitForExit(); }
    }
}
'@
    $script:StubExe = Join-Path $script:StubDir 'claude.exe'
    $compileOut = & $csc /nologo "/out:$script:StubExe" $src 2>&1
    if (-not (Test-Path -LiteralPath $script:StubExe)) {
        throw "failed to compile timeout/spawn test stub: $($compileOut -join "`n")"
    }

    # HARD GUARD. Get-ClaudePath (advisor-bridge.ps1) falls back to
    # $HOME\.local\bin\claude.exe when PATH resolution finds nothing, and that
    # fallback is not reachable by a PATH prepend at all. So a failed prepend
    # in Invoke-RealSpawn or the raw-ProcessStartInfo timeout/orphan harnesses
    # below is not a test failure - it silently routes the spawn to the real,
    # PAID CLI at the 240s default timeout. Compiling the stub only proves it
    # was PRODUCED, not that PATH resolution actually finds it; this proves
    # resolution, in the exact child-process shape (a fresh pwsh with the
    # prepend applied) every spawn test below uses.
    $script:Resolved = & pwsh -NoProfile -Command "
        `$env:PATH = '$script:StubDir;' + `$env:PATH
        (Get-Command claude -ErrorAction SilentlyContinue).Source"
    if ($script:Resolved -ne $script:StubExe) {
        throw "stub shadowing failed: 'claude' resolved to '$script:Resolved', not the stub at '$script:StubExe'. Refusing to run the spawn tests - they would invoke the real, paid CLI."
    }

    function Set-StubMode {
        param([string]$Mode)
        Set-Content -LiteralPath (Join-Path $script:StubDir 'mode.txt') -Value $Mode
    }

    function Invoke-RealSpawn {
        # Drives the ACTUAL spawn path (no -EnvelopeFile) against the compiled
        # stub on PATH, standing in for `claude`. Safe to capture with
        # `2>&1` for the non-orphan modes used here: none of them leaves a
        # grandchild alive after the child exits, so there is no descendant
        # holding a duplicate of this capture's own pipe open past the
        # child's own exit - the hazard documented at length in the 'timeout'
        # Describe below, which is why THAT test uses a raw, unredirected
        # ProcessStartInfo instead of this helper.
        param([string]$Mode, [string]$SessionFixture = 'basic.jsonl', [string[]]$Extra = @())
        $h = New-FixtureHome -SessionFixture $SessionFixture
        Set-StubMode $Mode
        $out = & pwsh -NoProfile -Command "
            `$env:PATH = '$script:StubDir;' + `$env:PATH
            `$env:CLAUDE_CONFIG_DIR = '$h'
            `$env:CLAUDE_CODE_SESSION_ID = 'fix-session'
            & '$script:Script' -ClaudeHome '$h' $($Extra -join ' ')
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
    It 'trips when the configured model is present but produced no output' {
        # Presence alone is not proof of work: fable's own entry carries
        # outputTokens: 0 while haiku carries 800 - haiku answered "in whole
        # or in part" and it would ship under fable's name without this check.
        $r = Invoke-WithEnvelope 'envelope-zero-output.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'model_guard'
    }
    It 'does not exempt a key merely shaped like the haiku prefix' {
        # claude-haiku-evil-proxy-glm has the 13-char 'claude-haiku-' prefix
        # but no version digit after it - the loose '^claude-haiku-' pattern
        # (case-insensitive, unbounded suffix) would wrongly exempt it.
        $r = Invoke-WithEnvelope 'envelope-fake-haiku.json'
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
    It 'reports no_envelope when result is empty, not ok with a blank reply' {
        # A clean parse with is_error:false and a valid modelUsage is not
        # enough - an empty result is not a reply, and exit 0 is documented as
        # "advice returned". Left unchecked this exits 0 with zero bytes on
        # stdout while logging verdict 'ok', which reports a successful
        # billed call for one that returned nothing to act on.
        $r = Invoke-WithEnvelope 'envelope-empty-result.json'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
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
    # Stub compilation lives in the top-level BeforeAll now (shared with the
    # 'spawn path envelope acquisition' Describe below); the reasoning for why
    # it must be a real .exe, not a .cmd/.bat, is there.

    It 'kills a slow child, exits 2, logs null cost and a real duration' {
        # Explicit, not relied-on-as-default: pin the shared stub to "hang"
        # regardless of what an earlier test in this run left in mode.txt, so
        # this test's behavior does not depend on file order.
        Set-StubMode 'hang'
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

Describe 'spawn path envelope acquisition' {
    # The -EnvelopeFile seam above bypasses the real spawn path entirely: the
    # -split / last-line-starting-with-'{' selection and its ConvertFrom-Json
    # are only ever executed here, by driving the compiled stub as `claude`
    # with no -EnvelopeFile at all.
    It 'parses a real envelope off spawned stdout and runs it through the same guard as the seam' {
        $r = Invoke-RealSpawn -Mode 'envelope'
        $r.Code | Should -Be 0
        $r.Text | Should -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'ok'
        # Not 'envelope-file': this row came from an actual child process, and
        # the seam's own test above already pins that canned rows are always
        # tagged so they can never be mistaken for a billed one.
        $r.Rows[-1].source | Should -Not -Be 'envelope-file'
    }

    It 'reports child_error on a nonzero exit from a real spawn' {
        $r = Invoke-RealSpawn -Mode 'nonzero'
        $r.Code | Should -Be 2
        $r.Text | Should -Not -Match 'SECRET-ADVICE-BODY'
        $r.Rows[-1].verdict | Should -Be 'child_error'
    }

    It 'reports child_error when the child exits without ever reading stdin' {
        # noreadstdin only forces a genuine broken-pipe fault (rather than a
        # write that silently completes into the pipe buffer) once the render
        # exceeds the OS pipe buffer - long.jsonl renders to ~20KB (checked via
        # -DryRun's chars_sent), comfortably past the ~4-64KB anonymous-pipe
        # buffer this needs to clear.
        $r = Invoke-RealSpawn -Mode 'noreadstdin' -SessionFixture 'long.jsonl' -Extra @('-TimeoutSec', '15')
        $r.Code | Should -Be 2
        $r.Rows[-1].verdict | Should -Be 'child_error'
    }

    It 'bounds the stdout/stderr drain and reaps a grandchild left holding the pipe after the child exits' {
        # This is Finding 1's own regression test. "orphan" mode is NOT "hang"
        # mode: the stub here starts ping and returns immediately WITHOUT
        # waiting for it, so $proc.WaitForExit(timeoutSeconds) succeeds almost
        # instantly - claude.exe itself really did exit - and it is the
        # UNBOUNDED stdout/stderr drain afterward that must be caught, not the
        # spawn-level timeout the 'timeout' Describe above already covers.
        #
        # Same `2>&1`-false-pass hazard as the 'timeout' Describe: ping
        # inherits a duplicate of any redirected pipe, so a raw, unredirected
        # ProcessStartInfo + bare WaitForExit() is required here too - see the
        # long comment on that test for the full mechanism.
        Set-StubMode 'orphan'
        $h = New-FixtureHome

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

        $start  = Get-Date
        $outer  = [System.Diagnostics.Process]::Start($outerPsi)
        $exited = $outer.WaitForExit(20000)

        $exited | Should -BeTrue -Because 'a bounded drain must let the wrapper exit near its own timeout, not hang for pings ~11s lifetime'
        $outer.ExitCode | Should -Be 2
        $row = (Get-Content (Join-Path $h 'advisor-bridge.log.jsonl') | Select-Object -Last 1) | ConvertFrom-Json
        # Both drains came back empty (nothing was ever written to stdout), so
        # this classifies as no_envelope, not timeout: the spawn itself
        # (WaitForExit) succeeded, only the post-exit drain had to be bounded.
        $row.verdict     | Should -Be 'no_envelope'
        $row.duration_ms | Should -BeGreaterThan 1500
        # The Finding-1 discriminator: an unbounded GetAwaiter().GetResult()
        # here blocks for ping's ~11s lifetime; a bounded drain returns at
        # ~2s. 8000ms comfortably separates the two without being so tight
        # that CI jitter trips it.
        $row.duration_ms | Should -BeLessThan 8000
        # The Blocking-2 discriminator: stdout and stderr must drain off ONE
        # shared remaining-budget deadline, not each get a fresh
        # $timeoutSeconds*1000 window of their own. Per-stage-fresh budgets
        # sum to up to 4x timeoutSeconds in this exact shape (write +
        # WaitForExit negligible here, then stdout times out at a full ~2s,
        # then stderr gets ANOTHER fresh ~2s) - about 4s total. A shared
        # deadline instead leaves stderr almost nothing once stdout has
        # already spent the budget, landing near timeoutSeconds itself
        # (~2-2.3s here). 3500ms sits between the two without the jitter risk
        # of pinning it to the ~2s figure exactly.
        $row.duration_ms | Should -BeLessThan 3500

        # The drain-timeout Kill($true) must reach the grandchild too, exactly
        # as the spawn-timeout Kill($true) does in the 'timeout' Describe -
        # otherwise this fix would trade a hung wrapper for an orphaned ping.
        Start-Sleep -Milliseconds 500
        @(Get-Process -Name 'PING' -ErrorAction SilentlyContinue |
            Where-Object { $_.StartTime -gt $start }).Count | Should -Be 0
    }
}
