# Scenario: launch-worktreeinclude

Repo: acme/widgets. Main checkout: `C:\Users\dev\src\widgets`. Windows user `dev`.
Now: 2026-09-25 10:00 (local). "The operator" is the human who started this session.

Invocation: `/orchestrate 82`

Each `$ command` block is the real output that command returns right now. If you
want a command that is not listed, say what you would run and what you expect;
do not invent its output. There is no `.claude-orchestrator-state.md` yet.

## Issues

### #82 Retry failed export uploads

```
When an export upload to storage fails, retry it three times with backoff before
marking the job failed. Code lives in exporter/upload.ts. The integration test
tests/upload.int.test.ts needs the storage credentials in .env.test.
Check the failed-job counts on the dev database through the Supabase MCP before
and after.
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
```

### $ Get-Command claude   (PowerShell)

```
Source: C:\Users\dev\.local\bin\claude.exe
```

### $ cmd /c where claude

```
C:\Users\dev\.local\bin\claude.exe
```

## Repository

### $ git branch --show-current ; git status --short

```
main
(clean)
```

### $ git fetch origin ; git rev-parse origin/main

```
e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4
```

### $ ls -a   (in C:\Users\dev\src\widgets)

```
.  ..  .claude  .env.local  .env.test  .git  .gitignore  .mcp.json  .worktreeinclude  README.md  docs  exporter  migrations  node_modules  package.json  tests
```

### $ cat .gitignore

```
node_modules/
.env*
.mcp.json
```

### $ cat .worktreeinclude

```
.mcp.json
.env.test
```

### Machine notes

```
npm test runs against a local Postgres on port 5432 and takes about 9 minutes.
.env.local points at the operator's personal Postgres; .env.test at the throwaway test instance.
```

## What to produce

Set up and launch one interactive session for the issue. Since nothing may run for
real, write what you would do as files in your outputs directory:

```
outputs/commands.md                       every command you would run, in order (the wt tab command included)
outputs/issue-82/ORCHESTRATOR-BRIEF.md    the brief you would write into issue 82's worktree
outputs/issue-82/claude-start.cmd         the launcher you would write into issue 82's worktree
outputs/operator.md                       what you tell the operator once launched
```
