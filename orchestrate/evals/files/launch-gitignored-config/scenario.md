# Scenario: launch-gitignored-config

Repo: acme/widgets. Main checkout: `C:\Users\dev\src\widgets`. Windows user `dev`.
Now: 2026-09-25 10:00 (local). "The operator" is the human who started this session.

Invocation: `/orchestrate 81`

Each `$ command` block is the real output that command returns right now. If you
want a command that is not listed, say what you would run and what you expect;
do not invent its output. There is no `.claude-orchestrator-state.md` yet.

## Issues

### #81 Row-level security for export_jobs

```
Add an RLS policy so a user can only read their own rows in export_jobs.
New migration in migrations/. Verify the policy on the dev database as an
authenticated test user before opening the PR.
```

## Repo CLAUDE.md (loaded in this session; the peer loads the same file)

```
## Database
All schema changes and database checks go through the Supabase MCP tools
(apply_migration, execute_sql) against the dev project. Never run
`supabase db push` or connect with psql.
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

### $ ls migrations | tail -2

```
0016_add_export_owner.sql
0017_add_export_retention.sql
```

### Machine notes

```
npm test runs against a local Postgres on port 5432 and takes about 9 minutes.
```

## What to produce

Set up and launch one interactive session for the issue. Since nothing may run for
real, write what you would do as files in your outputs directory:

```
outputs/commands.md                       every command you would run, in order (the wt tab command included)
outputs/issue-81/ORCHESTRATOR-BRIEF.md    the brief you would write into issue 81's worktree
outputs/issue-81/claude-start.cmd         the launcher you would write into issue 81's worktree
outputs/operator.md                       what you tell the operator once launched
```
