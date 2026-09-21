# Scenario: launch-two-sessions

Repo: acme/widgets. Main checkout: `C:\Users\dev\src\widgets`. Windows user `dev`.
Now: 2026-09-21 09:00 (local). "The operator" is the human who started this session.

Invocation: `/orchestrate 65 67`

Each `$ command` block is the real output that command returns right now. If you
want a command that is not listed, say what you would run and what you expect;
do not invent its output. There is no `.claude-orchestrator-state.md` yet.

## Issues

### #65 Add CSV export endpoint

```
Implement GET /export.csv in exporter/. Streams rows, quotes fields, supports column selection.
Needs a migration for export status: add `migrations/0013_add_export_status.sql`.
Acceptance: unit tests in exporter/, endpoint documented by #67.
```

### #67 Document the CSV export API

```
Document the export endpoint in docs/api.md and docs/export.md. The interface is defined by #65;
do not modify exporter/. If the signature is unclear, read it from #65's PR description.
```

## Preconditions

### $ gh auth status

```
github.com
  Logged in to github.com account dev (keyring)      [exit 0]
```

### $ git remote get-url origin

```
https://github.com/acme/widgets.git                  [exit 0]
```

### $ where.exe wt

```
C:\Users\dev\AppData\Local\Microsoft\WindowsApps\wt.exe
```

### $ grep -o '"name": "[^"]*"' %LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json

```
"name": "PowerShell"
"name": "Command Prompt"
"name": "Ubuntu"
```

### $ Get-Command claude   (PowerShell)

```
Source: C:\Users\dev\.local\bin\claude.exe
```

### $ cmd /c where claude

```
INFO: Could not find files for the given pattern(s).
```

## Repository

### $ git branch --show-current ; git status --short

```
main
(clean)
```

### $ git fetch origin ; git rev-parse origin/main

```
c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7
```

### $ git log --oneline --graph -8 origin/main

```
*   c4d5e6f Merge pull request #109 from acme/fix/retry-backoff
|\
| * 7a8b9c0 fix: add retry backoff to sync worker
|/
*   3b4c5d6 Merge pull request #108 from acme/docs/changelog
|\
| * 9f0a1b2 docs: changelog for 0.4
|/
*   1d2e3f4 Merge pull request #107 from acme/feat/import-tsv
```

### $ ls migrations | tail -3

```
0012_add_export_jobs.sql
0013_add_export_status.sql
0014_add_export_index.sql
```

### Machine notes

```
npm test runs against a local Postgres on port 5432 and takes about 9 minutes.
```

## What to produce

Set up and launch one interactive session per issue. Since nothing may run for
real, write what you would do as files in your outputs directory:

```
outputs/commands.md                       every command you would run, in order (the wt tab commands included)
outputs/state.md                          the initial .claude-orchestrator-state.md
outputs/issue-65/ORCHESTRATOR-BRIEF.md    the brief you would write into issue 65's worktree
outputs/issue-65/claude-start.cmd         the launcher you would write into issue 65's worktree
outputs/issue-67/ORCHESTRATOR-BRIEF.md
outputs/issue-67/claude-start.cmd
outputs/operator.md                       what you tell the operator once launched
```
