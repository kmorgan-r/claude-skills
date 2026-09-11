#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

# The wrapper's run log, exercised against a fake `ollama` so no case reaches a
# model endpoint, and against OLLAMA_WORKERS_HOME so no case reads the real
# state file or writes a row into the real ~/.claude/ollama-workers.log.jsonl.

BeforeAll {
    $script:Wrapper = Join-Path $PSScriptRoot '..' 'scripts' 'ollama-worker.ps1'
    $script:Root    = Join-Path ([System.IO.Path]::GetTempPath()) ("ow-wrap-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

    # State + overlay the preflight needs.
    $script:Home_ = Join-Path $Root 'home'
    New-Item -ItemType Directory -Path $Home_ -Force | Out-Null
    @{ enabled = $true; model = 'fake-model:cloud'; maxTurns = 25 } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $Home_ 'ollama-workers.json')
    '{}' | Set-Content -LiteralPath (Join-Path $Home_ 'ollama-settings.json')
    $script:Log = Join-Path $Home_ 'ollama-workers.log.jsonl'

    # A fake `ollama` first on PATH. FAKE_OLLAMA_MODE picks its behaviour.
    $bin = Join-Path $Root 'bin'
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    @'
param()
switch ($env:FAKE_OLLAMA_MODE) {
    'sleep'   { Start-Sleep -Seconds 120 }
    'nothing' { }
    default   { '{"type":"result","is_error":false,"num_turns":3,"duration_ms":1234,"session_id":"sess-fake","result":"done"}' }
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

    function Invoke-Wrapper([string[]]$Extra = @()) {
        $errFile = Join-Path $script:Root ("err-" + [guid]::NewGuid().ToString('N') + '.txt')
        $out = & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -Label t @Extra 2>$errFile
        [pscustomobject]@{
            Code    = $LASTEXITCODE
            Verdict = ($out | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1 | ConvertFrom-Json)
            Stderr  = (Get-Content -Raw -LiteralPath $errFile -ErrorAction SilentlyContinue)
        }
    }
}

AfterAll {
    $env:PATH = $script:SavedPath
    Remove-Item Env:\OLLAMA_WORKERS_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -LiteralPath $script:Root -ErrorAction SilentlyContinue
}

Describe 'run log' {
    BeforeEach { Remove-Item Env:\FAKE_OLLAMA_MODE -ErrorAction SilentlyContinue }

    It 'writes a start row and a run row that share the verdict''s run_id' {
        $r = Invoke-Wrapper
        $r.Code | Should -Be 0
        $r.Verdict.run_id | Should -Not -BeNullOrEmpty
        $rows = Get-Rows $r.Verdict.run_id
        @($rows.event) | Should -Be @('start', 'run')
        $rows[1].session_id | Should -Be 'sess-fake'
    }

    It 'names the run_id on stderr before the worker runs' {
        $r = Invoke-Wrapper
        $r.Stderr | Should -Match ("run_id=" + [regex]::Escape($r.Verdict.run_id))
    }

    It 'keeps run_id on the verdict when the child prints no result envelope' {
        $env:FAKE_OLLAMA_MODE = 'nothing'
        $r = Invoke-Wrapper
        $r.Code | Should -Be 2
        $r.Verdict.escalate | Should -BeTrue
        $r.Verdict.run_id | Should -Not -BeNullOrEmpty
        @((Get-Rows $r.Verdict.run_id).event) | Should -Be @('start', 'run')
    }

    It 'leaves an unpaired start row when the wrapper is killed mid-run' {
        $env:FAKE_OLLAMA_MODE = 'sleep'
        $before = if (Test-Path -LiteralPath $script:Log) { @(Get-Content -LiteralPath $script:Log).Count } else { 0 }
        $p = Start-Process pwsh -ArgumentList @('-NoProfile', '-File', $script:Wrapper, '-BriefFile', $script:Brief, '-Cwd', $script:Wt, '-Label', 'killme') -PassThru -WindowStyle Hidden
        $start = $null
        for ($i = 0; $i -lt 60 -and -not $start; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Path -LiteralPath $script:Log) {
                $start = @(Get-Content -LiteralPath $script:Log | Select-Object -Skip $before |
                    ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.event -eq 'start' -and $_.label -eq 'killme' }) | Select-Object -First 1
            }
        }
        taskkill /T /F /PID $p.Id | Out-Null
        Start-Sleep -Milliseconds 500

        $start | Should -Not -BeNullOrEmpty
        @((Get-Rows $start.run_id).event) | Should -Be @('start')
    }

    It 'writes no start row for a dry run' {
        $before = if (Test-Path -LiteralPath $script:Log) { @(Get-Content -LiteralPath $script:Log).Count } else { 0 }
        & pwsh -NoProfile -File $script:Wrapper -BriefFile $script:Brief -Cwd $script:Wt -DryRun | Out-Null
        $after = if (Test-Path -LiteralPath $script:Log) { @(Get-Content -LiteralPath $script:Log).Count } else { 0 }
        $after | Should -Be $before
    }
}
