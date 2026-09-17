#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

# The wrapper's bounds - turn cap, time limit, concurrency cap, process-tree
# cleanup - and its run log, exercised against a fake `ollama` so no case
# reaches a model endpoint, and against OLLAMA_WORKERS_HOME so no case reads the
# real state file or writes a row into the real ~/.claude/ollama-workers.log.jsonl.

BeforeAll {
    $script:Wrapper = (Resolve-Path (Join-Path $PSScriptRoot '..' 'scripts' 'ollama-worker.ps1')).Path
    $script:Root    = Join-Path ([System.IO.Path]::GetTempPath()) ("ow-wrap-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $script:Pwsh    = (Get-Process -Id $PID).Path

    $script:Home_ = Join-Path $Root 'home'
    New-Item -ItemType Directory -Path $Home_ -Force | Out-Null
    '{}' | Set-Content -LiteralPath (Join-Path $Home_ 'ollama-settings.json')
    $script:Log = Join-Path $Home_ 'ollama-workers.log.jsonl'

    function Set-State([hashtable]$extra = @{}) {
        $s = @{ enabled = $true; model = 'fake-model:cloud'; maxTurns = 25 }
        foreach ($k in $extra.Keys) { $s[$k] = $extra[$k] }
        $s | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $script:Home_ 'ollama-workers.json')
    }

    # A fake `ollama` first on PATH. FAKE_OLLAMA_MODE picks its behaviour, and
    # FAKE_PID_DIR is where it records its own PID and its grandchild's, so a
    # test can check that nothing it started outlives the wrapper's verdict.
    $bin = Join-Path $Root 'bin'
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    @'
param()
$envelope = '{"type":"result","subtype":"success","is_error":false,"num_turns":__TURNS__,"duration_ms":1234,"session_id":"sess-fake","result":"done"}'
function Start-Grandchild {
    # Detached: nothing waits on it, and once this script exits its parent is
    # gone - the shape of a dev server a worker starts and never stops.
    $pw = (Get-Process -Id $PID).Path
    $gc = Start-Process -FilePath $pw -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 90' -WindowStyle Hidden -PassThru
    if ($env:FAKE_PID_DIR) { Set-Content -LiteralPath (Join-Path $env:FAKE_PID_DIR 'grandchild.pid') -Value $gc.Id }
}
if ($env:FAKE_PID_DIR) {
    Set-Content -LiteralPath (Join-Path $env:FAKE_PID_DIR 'fake.pid') -Value $PID
    Set-Content -LiteralPath (Join-Path $env:FAKE_PID_DIR 'args.txt') -Value ($args -join "`n")
}
switch ($env:FAKE_OLLAMA_MODE) {
    'sleep'    { Start-Grandchild; Start-Sleep -Seconds 90 }
    'orphan'   { Start-Grandchild; $envelope.Replace('__TURNS__', '3') }
    'nothing'  { }
    'maxturns' { '{"type":"result","subtype":"error_max_turns","is_error":true,"num_turns":26,"duration_ms":500,"session_id":"sess-cap","errors":["Reached maximum number of turns (25)"]}'; exit 1 }
    'turns'    { $envelope.Replace('__TURNS__', $env:FAKE_TURNS) }
    default    { $envelope.Replace('__TURNS__', '3') }
}
'@ | Set-Content -LiteralPath (Join-Path $bin 'fake-ollama.ps1')
    "@pwsh -NoProfile -File `"%~dp0fake-ollama.ps1`" %*" | Set-Content -LiteralPath (Join-Path $bin 'ollama.cmd')

    # A linked worktree - the only -Cwd the preflight accepts.
    $repo = Join-Path $Root 'repo'
    git init -q $repo
    git -C $repo -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
    $script:Wt = Join-Path $Root 'wt'
    git -C $repo worktree add -q --detach $Wt 2>$null

    $script:Brief = Join-Path $Root 'brief.md'
    'Reply OK.' | Set-Content -LiteralPath $Brief

    $script:SavedPath = $env:PATH
    $env:PATH = "$bin;$env:PATH"
    $env:OLLAMA_WORKERS_HOME = $Home_

    function Get-Rows([string]$runId) {
        if (-not (Test-Path -LiteralPath $script:Log)) { return @() }
        @(Get-Content -LiteralPath $script:Log | ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.run_id -eq $runId })
    }

    function Get-LabelRows([string]$label) {
        if (-not (Test-Path -LiteralPath $script:Log)) { return @() }
        @(Get-Content -LiteralPath $script:Log | ForEach-Object { $_ | ConvertFrom-Json } |
            Where-Object { $_.label -eq $label })
    }

    function New-PidDir {
        $d = Join-Path $script:Root ("pids-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        $env:FAKE_PID_DIR = $d
        $d
    }

    function Read-Pid([string]$dir, [string]$name) {
        $f = Join-Path $dir "$name.pid"
        for ($i = 0; $i -lt 40 -and -not (Test-Path -LiteralPath $f); $i++) { Start-Sleep -Milliseconds 250 }
        [int](Get-Content -LiteralPath $f -ErrorAction Stop | Select-Object -First 1)
    }

    # True once every PID has exited, waiting up to $seconds for it.
    function Test-AllGone([int[]]$pids, [int]$seconds = 10) {
        for ($i = 0; $i -lt ($seconds * 4); $i++) {
            if (-not ($pids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })) { return $true }
            Start-Sleep -Milliseconds 250
        }
        $false
    }

    function Invoke-Wrapper([string]$Label = 't', [string[]]$Extra = @()) {
        $errFile = Join-Path $script:Root ("err-" + [guid]::NewGuid().ToString('N') + '.txt')
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $out = & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -Label $Label @Extra 2>$errFile
        [pscustomobject]@{
            Code    = $LASTEXITCODE
            Seconds = $sw.Elapsed.TotalSeconds
            Verdict = ($out | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1 | ConvertFrom-Json)
            Stderr  = (Get-Content -Raw -LiteralPath $errFile -ErrorAction SilentlyContinue)
        }
    }

    # A wrapper in the background with stdout and stderr merged into one file,
    # which is what a harness's background-command output file looks like.
    function Start-BackgroundWrapper([string]$Label, [string]$OutFile, [string[]]$Extra = @()) {
        $line = "pwsh -NoProfile -File `"$script:Wrapper`" -BriefFile `"$script:Brief`" -Cwd `"$script:Wt`" -Label $Label $($Extra -join ' ') > `"$OutFile`" 2>&1"
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/c', "`"$line`"" -WindowStyle Hidden -PassThru
    }

    function Wait-StartRow([string]$label, [int]$seconds = 40) {
        for ($i = 0; $i -lt ($seconds * 4); $i++) {
            $row = Get-LabelRows $label | Where-Object event -eq 'start' | Select-Object -First 1
            if ($row) { return $row }
            Start-Sleep -Milliseconds 250
        }
        $null
    }

    function Invoke-Await([string]$OutFile, [string[]]$Extra = @()) {
        $out = & pwsh -NoProfile -File $script:Wrapper -Await $OutFile @Extra 2>&1
        [pscustomobject]@{
            Code    = $LASTEXITCODE
            State   = ($out | Where-Object { "$_" -like 'STATE: *' } | Select-Object -First 1)
            Verdict = ($out | Where-Object { "$_".TrimStart().StartsWith('{"ok"') } | Select-Object -Last 1 | ForEach-Object { "$_" | ConvertFrom-Json })
            Text    = ($out | ForEach-Object { "$_" }) -join "`n"
        }
    }
}

AfterAll {
    $env:PATH = $script:SavedPath
    Remove-Item Env:\OLLAMA_WORKERS_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:\FAKE_PID_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:\FAKE_TURNS -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -LiteralPath $script:Root -ErrorAction SilentlyContinue
}

Describe 'settings' {
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }

    It 'passes --max-turns from state to the headless run' {
        Set-State @{ maxTurns = 17 }
        $plan = & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -DryRun | ConvertFrom-Json
        $i = [array]::IndexOf([string[]]$plan.args, '--max-turns')
        $i | Should -BeGreaterThan ([array]::IndexOf([string[]]$plan.args, '--'))
        $plan.args[$i + 1] | Should -Be '17'
    }

    It 'defaults timeoutMinutes to 25 and maxConcurrent to 1 when state omits them' {
        $probe = & pwsh -NoProfile -File $script:Wrapper -Probe -Cwd $script:Wt | ConvertFrom-Json
        $probe.timeout_minutes | Should -Be 25
        $probe.max_concurrent | Should -Be 1
    }

    It 'defaults maxTurns to 100 when state omits it' {
        @{ enabled = $true; model = 'fake-model:cloud' } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $script:Home_ 'ollama-workers.json')
        $plan = & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -DryRun | ConvertFrom-Json
        $plan.maxTurns | Should -Be 100
        $plan.args[[array]::IndexOf([string[]]$plan.args, '--max-turns') + 1] | Should -Be '100'
    }

    It 'reads timeoutMinutes and maxConcurrent from state' {
        Set-State @{ timeoutMinutes = 40; maxConcurrent = 3 }
        $plan = & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -DryRun | ConvertFrom-Json
        $plan.timeoutMinutes | Should -Be 40
        $plan.maxConcurrent | Should -Be 3
    }
}

Describe 'turn cap' {
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }

    It 'maps a run stopped by --max-turns to an escalation that names the cap' {
        $env:FAKE_OLLAMA_MODE = 'maxturns'
        $r = Invoke-Wrapper -Label 'cap-hit'
        $r.Code | Should -Be 2
        $r.Verdict.escalate | Should -BeTrue
        $r.Verdict.reason | Should -Be 'max_turns_25'
        (Get-Rows $r.Verdict.run_id | Where-Object event -eq 'run').reason | Should -Be 'max_turns_25'
    }

    It 'still escalates a successful envelope that reports more turns than the cap' {
        $env:FAKE_OLLAMA_MODE = 'turns'; $env:FAKE_TURNS = '30'
        $r = Invoke-Wrapper -Label 'over'
        $r.Code | Should -Be 2
        $r.Verdict.reason | Should -Be 'turns_30_over_25'
    }
}

Describe 'time limit and process tree' {
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }
    AfterEach { Remove-Item Env:\FAKE_PID_DIR -ErrorAction SilentlyContinue }

    It 'returns a timeout verdict and kills the whole worker tree when the limit passes' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $dir = New-PidDir
        $r = Invoke-Wrapper -Label 'slow' -Extra @('-TimeoutMinutes', '0.1')

        $r.Seconds | Should -BeLessThan 45
        $r.Code | Should -Be 2
        $r.Verdict.escalate | Should -BeTrue
        $r.Verdict.reason | Should -Be 'timeout_0.1m'
        Test-AllGone @((Read-Pid $dir 'fake'), (Read-Pid $dir 'grandchild')) | Should -BeTrue
    }

    It 'writes a run row for a timed-out run' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $null = New-PidDir
        $r = Invoke-Wrapper -Label 'slow-log' -Extra @('-TimeoutMinutes', '0.1')
        $rows = Get-Rows $r.Verdict.run_id
        @($rows.event) | Should -Be @('start', 'run')
        $rows[1].reason | Should -Be 'timeout_0.1m'
    }

    It 'does not wait for a process the worker left running, and kills it' {
        # hero-task-11: the worker finished, a server it started kept running,
        # and a wait on the whole tree blocked for 8 hours.
        $env:FAKE_OLLAMA_MODE = 'orphan'
        $dir = New-PidDir
        $r = Invoke-Wrapper -Label 'orphan' -Extra @('-TimeoutMinutes', '2')

        $r.Seconds | Should -BeLessThan 45
        $r.Code | Should -Be 0
        $r.Verdict.ok | Should -BeTrue
        Test-AllGone @(Read-Pid $dir 'grandchild') | Should -BeTrue
        (Get-Rows $r.Verdict.run_id | Where-Object event -eq 'run').leftover_processes | Should -BeGreaterThan 0
    }

    It 'takes the worker tree down when the wrapper itself is killed' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $dir = New-PidDir
        $out = Join-Path $script:Root 'killed-wrapper.out'
        $shell = Start-BackgroundWrapper -Label 'killme' -OutFile $out -Extra @('-TimeoutMinutes', '5')
        $start = Wait-StartRow 'killme'
        $start | Should -Not -BeNullOrEmpty
        $fake = Read-Pid $dir 'fake'; $grandchild = Read-Pid $dir 'grandchild'

        # The wrapper process only, not its tree - what a harness kill can do.
        Stop-Process -Id $start.wrapper_pid -Force
        Test-AllGone @($fake, $grandchild) | Should -BeTrue
        @((Get-Rows $start.run_id).event) | Should -Be @('start')
        Stop-Process -Id $shell.Id -Force -ErrorAction SilentlyContinue
    }
}

Describe 'run log' {
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }

    It 'writes a start row and a run row that share the verdict''s run_id' {
        $r = Invoke-Wrapper
        $r.Code | Should -Be 0
        $r.Verdict.run_id | Should -Not -BeNullOrEmpty
        $rows = Get-Rows $r.Verdict.run_id
        @($rows.event) | Should -Be @('start', 'run')
        $rows[1].session_id | Should -Be 'sess-fake'
    }

    It 'names the run_id, wrapper pid and time limit on stderr before the worker runs' {
        $r = Invoke-Wrapper
        $r.Stderr | Should -Match ("run_id=" + [regex]::Escape($r.Verdict.run_id) + " started")
        $r.Stderr | Should -Match 'wrapper_pid \d+, timeout_s 1500'
    }

    It 'keeps run_id on the verdict when the child prints no result envelope' {
        $env:FAKE_OLLAMA_MODE = 'nothing'
        $r = Invoke-Wrapper
        $r.Code | Should -Be 2
        $r.Verdict.escalate | Should -BeTrue
        $r.Verdict.run_id | Should -Not -BeNullOrEmpty
        @((Get-Rows $r.Verdict.run_id).event) | Should -Be @('start', 'run')
    }

    It 'writes no start row for a dry run' {
        $before = if (Test-Path -LiteralPath $script:Log) { @(Get-Content -LiteralPath $script:Log).Count } else { 0 }
        & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -DryRun | Out-Null
        $after = if (Test-Path -LiteralPath $script:Log) { @(Get-Content -LiteralPath $script:Log).Count } else { 0 }
        $after | Should -Be $before
    }
}

Describe 'concurrency cap' {
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }
    AfterEach { Remove-Item Env:\FAKE_PID_DIR -ErrorAction SilentlyContinue }

    It 'refuses a dispatch while maxConcurrent workers are running, then accepts one after' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $null = New-PidDir
        $out = Join-Path $script:Root 'holder.out'
        $shell = Start-BackgroundWrapper -Label 'holder' -OutFile $out -Extra @('-TimeoutMinutes', '5')
        $holder = Wait-StartRow 'holder'
        $holder | Should -Not -BeNullOrEmpty

        Remove-Item Env:\FAKE_OLLAMA_MODE
        $r = Invoke-Wrapper -Label 'second'
        $r.Code | Should -Be 2
        $r.Verdict.escalate | Should -BeTrue
        $r.Verdict.reason | Should -Be 'concurrency_cap'
        $rows = Get-LabelRows 'second'
        @($rows.event) | Should -Be @('refused')
        $rows[0].max_concurrent | Should -Be 1

        Stop-Process -Id $holder.wrapper_pid -Force
        Stop-Process -Id $shell.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        (Invoke-Wrapper -Label 'third').Code | Should -Be 0
    }
}

Describe '-Await' {
    BeforeAll {
        function New-OutFile([string]$text) {
            $f = Join-Path $script:Root ("await-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.out')
            Set-Content -LiteralPath $f -Value $text
            $f
        }
        function New-StartLine([string]$runId, [int]$wrapperPid, [int]$timeoutS, [datetime]$at) {
            "ollama-worker: run_id=$runId started (label 'aw', model fake-model:cloud, wrapper_pid $wrapperPid, timeout_s $timeoutS, at $($at.ToUniversalTime().ToString('o')))"
        }
    }
    BeforeEach { Set-State; Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }
    AfterEach { Remove-Item Env:\FAKE_PID_DIR -ErrorAction SilentlyContinue }

    It 'reports finished with the verdict line from a real run''s output' {
        $out = Join-Path $script:Root 'real-finished.out'
        $shell = Start-BackgroundWrapper -Label 'aw-real' -OutFile $out
        $shell.WaitForExit(60000) | Should -BeTrue
        $a = Invoke-Await $out @('-PollSeconds', '5')
        $a.State | Should -Be 'STATE: finished'
        $a.Verdict.ok | Should -BeTrue
        $a.Verdict.session_id | Should -Be 'sess-fake'
    }

    It 'reports waiting while the wrapper that printed the start line is alive' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $null = New-PidDir
        $out = Join-Path $script:Root 'real-waiting.out'
        $shell = Start-BackgroundWrapper -Label 'aw-wait' -OutFile $out -Extra @('-TimeoutMinutes', '5')
        $start = Wait-StartRow 'aw-wait'
        for ($i = 0; $i -lt 40 -and -not ((Get-Content -Raw -LiteralPath $out -ErrorAction SilentlyContinue) -match 'started'); $i++) { Start-Sleep -Milliseconds 250 }

        $a = Invoke-Await $out @('-PollSeconds', '3')
        $a.State | Should -Be 'STATE: waiting'

        Stop-Process -Id $start.wrapper_pid -Force
        Stop-Process -Id $shell.Id -Force -ErrorAction SilentlyContinue
    }

    It 'synthesizes a verdict and a run row when the wrapper died without one' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $null = New-PidDir
        $out = Join-Path $script:Root 'real-died.out'
        $shell = Start-BackgroundWrapper -Label 'aw-died' -OutFile $out -Extra @('-TimeoutMinutes', '5')
        $start = Wait-StartRow 'aw-died'
        Stop-Process -Id $start.wrapper_pid -Force
        $shell.WaitForExit(10000) | Out-Null

        $a = Invoke-Await $out @('-PollSeconds', '20')
        $a.State | Should -Be 'STATE: finished'
        $a.Verdict.escalate | Should -BeTrue
        $a.Verdict.reason | Should -Be 'wrapper_died'
        $a.Verdict.run_id | Should -Be $start.run_id
        $run = @(Get-Rows $start.run_id | Where-Object event -eq 'run')
        $run.Count | Should -Be 1
        $run[0].reason | Should -Be 'wrapper_died'
    }

    It 'names the exit code when the harness recorded one' {
        $runId = [guid]::NewGuid().ToString()
        $dead = Start-Process -FilePath $script:Pwsh -ArgumentList '-NoProfile', '-Command', 'exit 0' -PassThru -WindowStyle Hidden
        $dead.WaitForExit()
        $out = New-OutFile ((New-StartLine $runId $dead.Id 1500 (Get-Date)) + "`n`n[exited with code 255]")
        $a = Invoke-Await $out @('-PollSeconds', '20')
        $a.State | Should -Be 'STATE: finished'
        $a.Verdict.reason | Should -Be 'wrapper_exit_255'
    }

    It 'kills an overdue wrapper and returns a verdict instead of waiting on it' {
        $stuck = Start-Process -FilePath $script:Pwsh -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 120' -PassThru -WindowStyle Hidden
        Start-Sleep -Milliseconds 500
        $runId = [guid]::NewGuid().ToString()
        $out = New-OutFile (New-StartLine $runId $stuck.Id 1 (Get-Date))

        $a = Invoke-Await $out @('-PollSeconds', '30', '-GraceSeconds', '2')
        $a.State | Should -Be 'STATE: finished'
        $a.Verdict.escalate | Should -BeTrue
        $a.Verdict.reason | Should -Be 'wrapper_overdue'
        Test-AllGone @($stuck.Id) | Should -BeTrue
    }

    It 'does not treat a reused PID as the wrapper' {
        # The PID in the start line now belongs to a process that started after
        # the wrapper did, so the wrapper is gone and must not be reported alive.
        $later = Start-Process -FilePath $script:Pwsh -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 60' -PassThru -WindowStyle Hidden
        $runId = [guid]::NewGuid().ToString()
        $out = New-OutFile (New-StartLine $runId $later.Id 1500 (Get-Date).AddMinutes(-5))

        $a = Invoke-Await $out @('-PollSeconds', '20')
        $a.Verdict.reason | Should -Be 'wrapper_died'
        Get-Process -Id $later.Id -ErrorAction SilentlyContinue | Should -Not -BeNullOrEmpty
        Stop-Process -Id $later.Id -Force
    }

    It 'reports no verdict when the wrapper refused before launching' {
        $out = New-OutFile "ollama-worker: ollama workers are disabled in C:\x\ollama-workers.json`n`n[exited with code 1]"
        $a = Invoke-Await $out @('-PollSeconds', '5')
        $a.State | Should -Be 'STATE: no verdict'
        $a.Code | Should -Be 1
        $a.Text | Should -Match 'workers are disabled'
    }
}
