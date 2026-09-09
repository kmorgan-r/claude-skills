#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

Describe 'test harness' {
    It 'runs Pester 5' {
        (Get-Module Pester).Version.Major | Should -BeGreaterOrEqual 5
    }
    It 'can see the fixtures directory' {
        Join-Path $PSScriptRoot 'fixtures' | Should -Exist
    }
}
