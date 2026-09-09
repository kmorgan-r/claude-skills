#Requires -Version 7
#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0' }
$ErrorActionPreference = 'Stop'

# Normalised at the read, not in each pattern: core.autocrlf is true and the repo
# has no .gitattributes, so a fresh clone can materialise SKILL.md with CRLF. These
# assertions are about what the document says, never about how it was checked out.
BeforeAll { $script:Skill = (Get-Content -Raw (Join-Path $PSScriptRoot '..' 'SKILL.md')) -replace "`r`n", "`n" }

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