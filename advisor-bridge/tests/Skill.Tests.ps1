#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

BeforeAll { $script:Skill = Get-Content -Raw (Join-Path $PSScriptRoot '..' 'SKILL.md') }

Describe 'SKILL.md' {
    It 'passes timeout: 300000 on the invocation line' {
        # The failure most likely to spoil first use: a real transcript exceeds
        # the Bash tool's 120s default and the caller sees a kill, not advice.
        $script:Skill | Should -Match 'timeout:\s*300000'
    }
    It 'documents on, off and status' {
        $script:Skill | Should -Match '(?m)^\*\*`on[`\s]'
        $script:Skill | Should -Match '(?m)^\*\*`off`'
        $script:Skill | Should -Match '(?m)^\*\*`status`'
    }
    It 'has frontmatter with a name and a description' {
        $script:Skill | Should -Match '(?s)^---.*\nname: advisor-bridge\n.*description: .*\n---'
    }
}