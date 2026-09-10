# Claude Code Skills

A personal collection of [Claude Code](https://claude.com/claude-code) skills.

Each subdirectory is a self-contained skill: a `SKILL.md` (the instructions Claude
follows at runtime) plus any bundled scripts, references, and evals.

| Skill | What it does |
|-------|--------------|
| [`climatepoint-contact-intelligence`](./climatepoint-contact-intelligence) | Turns a raw contact CSV into a scored sales map: enriches contacts via web research (Title, LinkedIn, Company, Summary, Headline), classifies by persona/need/score, and emits a sales-ready output file. The scorer that `find-cold-leads` hands off to. |
| [`find-cold-leads`](./find-cold-leads) | Finds and **qualifies** B2B cold leads on free signals, spends scarce Apollo enrichment credits only on rows that fit the ICP, tags each with a region-aware compliance posture, and exports a classifier-ready / Odoo-ready sheet. |
| [`linkedin-outreach-odoo`](./linkedin-outreach-odoo) | Picks up where `find-cold-leads` leaves off: reads eligible `mailing.contact` leads from Odoo, drafts a personalized LinkedIn connection note per lead, sends connection requests via the ConnectSafely API (dry-run by default), and writes outreach state back to Odoo. |
| [`fix-pr-reviews`](./fix-pr-reviews) | Fetches the most recent GitHub PR review comments and systematically addresses each one — no copy-pasting from the PR. Supports a `--loop` mode. |
| [`ship`](./ship) | Conductor that drives the post-brainstorm dev pipeline hands-off — spec-review, plan, plan-review, implementation, PR, then the `fix-pr-reviews` loop — resuming across `/clear` via a state file, stopping only on failure or final merge. |
| [`ship-fleet`](./ship-fleet) | Runs up to 10 `/ship` pipelines in parallel: one headless Claude Code instance per GitHub issue, each in its own git worktree, coordinated by a fleet manifest and a polling monitor with crash recovery. Human gates (merge, DB acks) stay human. **Windows-only** (PowerShell). |
| [`reviewing-plans`](./reviewing-plans) | Reviews a written implementation plan before execution: dispatches 2–5 domain-specific reviewer agents in parallel, consolidates findings, and applies approved fixes to the plan file. |
| [`esg-longitudinal`](./esg-longitudinal) | Tracks a company's ESG / CSR / sustainability commitments over time using **free** public data: finds sustainability/annual report PDFs, extracts targets and metrics into a tidy time-series with source + period + verbatim quote per value, saves a timestamped snapshot, and diffs against earlier snapshots to surface what changed. Re-runnable next year; scales from one company toward tens of thousands. |
| [`ollama-workers`](./ollama-workers) | Lets an Anthropic-model orchestrator hand short-turn implementer tasks to an Ollama cloud model (GLM, Kimi) running in a separate headless Claude Code process. `/ollama-workers on\|off\|status` is the whole interface; reviewers stay on Anthropic. **Windows-only** (PowerShell). |
| [`advisor-bridge`](./advisor-bridge) | Lets a Claude Code session running on a non-Anthropic backend (`ollama launch claude` — GLM, Kimi) reach an Anthropic model for advice, by rendering the session's own transcript into a scrubbed `claude -p` child process. The built-in `advisor` tool is disabled there and would be GLM advising GLM anyway. **Windows-only** (PowerShell). |

## Install

Skills load from `~/.claude/skills/` (global) or `<project>/.claude/skills/`
(per-project). Copy the skill you want into one of those, then invoke it:

```bash
# global install
cp -r find-cold-leads ~/.claude/skills/

# then in Claude Code
/find-cold-leads        # or /fix-pr-reviews
```

## Notes per skill

### climatepoint-contact-intelligence
- **The scorer** that `find-cold-leads` hands off to (its classifier-ready columns
  feed this skill's input). Run it standalone on any raw contact CSV too.
- **Needs** a web-search provider via env var (`SERPER_API_KEY` / `TAVILY_API_KEY` /
  `BRAVE_API_KEY`); no keys stored in the repo.
- **Install the whole directory** (`cp -r climatepoint-contact-intelligence …`), not
  just `SKILL.md`: the bundled `references/` scripts and `evals/` set are load-bearing.

### find-cold-leads
- **Hands off to** [`climatepoint-contact-intelligence`](./climatepoint-contact-intelligence)
  (the scorer, included in this repo). Without it, the handoff still produces the
  classifier-ready columns; the column-conformance test validates against a pinned
  snapshot instead of the live classifier source.
- **Needs** the Apollo MCP server for enrichment (Mode A). Open-web fallback (Mode O)
  uses a search provider via env var (`SERPER_API_KEY` / `TAVILY_API_KEY`); no keys
  are stored in the repo.
- **Tests:** `cd find-cold-leads && python -m pytest scripts/test_lead_crawler.py -q`
  (deterministic, offline, spends no credits).
- **Evals:** a blind qualification set in `evals/` scored by `score_qualification.py`
  (gold labels kept in a separate private file; no eval issues a live Apollo call).

### linkedin-outreach-odoo
- **Downstream of** `find-cold-leads`: run that first, review the workbook, import
  the `odoo_ready` leads into Odoo `mailing.contact` (LinkedIn URL → `x_linkedin_url`).
- **Needs** the `climatepoint-odoo` MCP server (reads/writes `mailing.contact` over
  JSON-RPC; no `odoo shell`) and the ConnectSafely API client + `linkedin_outreach.py`
  send script (kept in the marketing repo, not here). Auth via env vars only
  (`ODOO_LOGIN`, `ODOO_API_KEY`, `CONNECTSAFELY_API_KEY`); no keys in the repo.
- **State machine:** eligibility and write-back ride the existing `x_lead_status`
  field (`New`/unset → `Attempting contact`) — no LinkedIn-specific Odoo fields to
  create.
- **Safety:** outreach is dry-run by default (`--send` to actually send, 90/week cap);
  Odoo write-back is gated by the MCP's two-step confirmation code. Both require
  explicit user confirmation before anything irreversible happens.
- **Untrusted lead text — pre-sanitize on import.** The per-lead pitch
  (`x_outreach_angle` / `matched_signal`) is free text summarized from Apollo-enriched,
  web-scraped sources with no sanitization, so it's an indirect prompt-injection
  surface. The skill screens it (a dedicated skip-evaluation pass *before* any note is
  drafted, treating the field as quoted data, never instructions), but pattern
  screening can be paraphrased around and **cannot fully close the surface**. For
  defense in depth, strip/escape instruction-like content and cap length on
  `x_outreach_angle` **at import time**, before it ever reaches this skill — the skill
  reads Odoo only and can't sanitize at the source itself. So it doesn't run past the
  gap silently: **Prerequisite 6 makes import-time sanitization a once-per-source gate on
  drafting mode** — only a source the operator confirms was pre-sanitized may seed notes
  from the pitch (personalized mode); an unconfirmed source falls back to structured-field
  / templated notes that never read the pitch at all, so there is no opt-in path that
  feeds unsanitized free text into a connection note.
- **Install the whole directory** (`cp -r linkedin-outreach-odoo …`), not just
  `SKILL.md`: the bundled `.gitignore` is load-bearing — it's the backstop that keeps
  exported lead PII out of git if you ever point the working files back into a repo.
  Lead CSVs and the outreach log default to `%TEMP%\linkedin-outreach\` (outside any
  git tree) regardless. Paths inside `SKILL.md` are machine-specific (`~\marketing`,
  `~\climatepoint-odoo-mcp`, …) — adjust them to your own layout on install.
- Invoke by asking to reach out to your Odoo cold leads on LinkedIn.

### fix-pr-reviews
- **Needs** the GitHub CLI (`gh`) authenticated.
- Invoke `/fix-pr-reviews` (optionally `--loop`) inside a repo with an open PR.

### ship
- **Part of the [superpowers](https://github.com/obra/superpowers) pipeline.** Delegates
  to other skills — `reviewing-plans` (P1, P3), `writing-plans` (P2),
  `subagent-driven-development` (P4), `finishing-a-development-branch` (P5), and
  `fix-pr-reviews` (P6); install those too or the phases that call them stall.
- **Run after** `/superpowers:brainstorming` produced a committed spec. Resumes an
  in-progress run from its state file across `/clear` or auto-compact.
- **P4 exit gate** runs the `lint` and `check:types` npm scripts *when the repo
  defines them* (absent scripts are skipped, not treated as failures) plus the
  change's own test files — so it works across repos without those scripts.
- **Composes with [`ollama-workers`](./ollama-workers)** when it is installed and on:
  P0 probes the directory and records the answer in state, and P4 cuts a short
  sibling worktree to dispatch implementers into — the wrapper refuses a primary
  checkout, which is all `/ship` ever runs in. Not installed or off is fine and
  costs nothing; either way the route is written down rather than assumed.
- Invoke `/ship` once; it runs phases P0–P7 hands-off.

### ship-fleet
- **Builds on [`ship`](./ship)** — install `ship` (and the skills it delegates to)
  first; each fleet instance runs the full `/ship` pipeline headless. Fleet consumes
  only ship's documented state-file contract; ship is never modified.
- **Needs** Windows with PowerShell 7 (`pwsh`) on PATH and the GitHub CLI (`gh`)
  authenticated. Spawn/liveness/kill mechanics are PowerShell — not portable to
  macOS/Linux as written.
- **Spawned instances run `claude -p --dangerously-skip-permissions`** in their own
  worktrees. Bare mode (no plan/spec on the issue) only accepts issues authored by
  OWNER/MEMBER/COLLABORATOR, and issue content is always treated as data, never
  instructions.
- Invoke `/ship-fleet <issue numbers>` (e.g. `/ship-fleet 101 102 103 --max 5`), then
  `/ship-fleet status|resume|cleanup` to manage the fleet.

### reviewing-plans
- Takes a path to an existing plan markdown file (or finds the most recently referenced
  one). Used standalone or as a `ship` phase.
- Invoke `/reviewing-plans` after a plan exists, before execution.

### esg-longitudinal
- **Free data only** — no API subscription required. Builds an assembled free stack:
  GLEIF LEI for entity resolution, structured lists (SBTi, Net Zero Tracker, CDP, TNFD,
  SEC EDGAR, WikiRate) for breadth, and a `find → fetch → extract` report-PDF chain for
  the detail those miss (most circular-economy and biodiversity metrics). Paid feeds
  (FMP, ESG Book, CSRHub, …) bolt on later as just another `source` — same schema, same
  diff. See [`references/data_sources.md`](./esg-longitudinal/references/data_sources.md).
- **Two time axes, snapshot-first.** `period` = the reporting year of a value;
  `retrieved_at` = the run date. Every run writes a timestamped snapshot CSV
  (`data/snapshots/<date>.csv`) so a future run has something to diff against. Backward
  trend comes from multi-year reports; forward change comes from diffing a later snapshot
  against an earlier one (`scripts/diff.py`).
- **Anti-hallucination gate is load-bearing.** `scripts/snapshot.py` rejects any
  `found`/`target` row lacking a value **and** `source_url` **and** verbatim `quote`;
  gaps are recorded as `not_found`, never guessed or interpolated. A value with no source
  and no quote is not a value.
- **Needs** Python with `ddgs` (report search), `requests`, and `PyMuPDF` (`fitz`) +
  `pdfplumber` (PDF text/tables) — all free, no keys. No credentials stored in the repo.
- **Install the whole directory** (`cp -r esg-longitudinal …`), not just `SKILL.md`: the
  bundled `scripts/`, `references/` (indicator packs + source catalog), and `evals/` are
  load-bearing.
- Invoke by asking to track a company's ESG/CSR/sustainability targets over time
  (e.g. "pull Philips' circular-economy targets over the last 10 years, with sources").
### ollama-workers
- **Why a child process, not a subagent.** A Claude Code process serves exactly one
  provider: `ollama launch claude` exports `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
  and all three `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` vars, and the CLI warns
  that any such auth source takes precedence over the claude.ai login. The Agent tool's
  `model` is an enum, and subagents run in-process, so no subagent can be pointed
  somewhere else. Keeping the orchestrator on a subscription therefore means spawning a
  worker, which also rules out routing the whole session through a proxy.
- **Needs** Windows with PowerShell 7 (`pwsh`), [Ollama](https://ollama.com) on PATH,
  and `ollama signin` for `:cloud` tags. Run `./ollama-workers/install.ps1` (`-DryRun`
  first) — a plain `cp -r` installs the skill but not the forwarder agent, the wrapper
  script, or the SessionStart hook. Existing state files are left alone; `settings.json`
  is re-serialized to add one SessionStart entry after a timestamped backup, and the
  rewrite is compared against that backup key by key and rolled back if anything moved.
- **Off by default, and invisible when off.** State lives in
  `~/.claude/ollama-workers.json`; the SessionStart hook prints the routing rule only
  when enabled, so a disabled install costs no context. When it is enabled the hook
  also probes the session's directory and says so when a dispatch from there would be
  refused — being on and being dispatchable are separate facts, and reporting only the
  first is what once made a whole plan skip the worker with nothing logged and nothing
  said. `enabled` is enforced by the
  wrapper, not just by the hook and the skill's prose: it exits 1 before launching
  anything unless the state file says `enabled: true`, so a dispatch on stale context
  cannot reach a third-party endpoint while the feature is off. A missing state file
  counts as off.
- **Implementers only.** Task reviewers, scoped re-reviews, the plan-document reviewer,
  the final code review, and fix-round escalation stay on Anthropic — use opus for the
  reviews. On Artificial Analysis, `glm-5.3-flash` scores 72 coding / 52 agentic against
  Claude Sonnet 5's 72 / 45, so a sonnet gate would not sit above the worker it grades.
- **The endpoint has no prompt caching**, so ~34K of system prompt is re-sent every turn
  and TTFT is ~20s. Turn count, not the benchmark index, decides fit: short-turn
  mechanical tasks with a complete brief go to the worker, multi-file and integration
  work stays in-process. Escalation is evidence-based (`is_error`, nonzero exit, or
  `num_turns > maxTurns`) and has two rungs — ollama model, then Anthropic. Never
  ollama-to-ollama.
- **Isolated config dir.** The worker runs under `CLAUDE_CONFIG_DIR=~/.claude-ollama-worker`
  with `plugins` junctioned in. Sessions live at `<config-dir>/projects/<cwd>/`, so
  sharing the caller's config dir would leave worker transcripts where the caller's next
  `claude --continue` would resume them — and a session produced by a non-Anthropic
  backend fails to resume against the Anthropic API.
- **The worker runs `--dangerously-skip-permissions`** — it has to edit files and run
  tests with nobody there to answer a prompt. Because an orchestrator picks `-Cwd` for
  every dispatch, that is enforced rather than documented: the wrapper accepts only a
  linked git worktree (`git rev-parse --git-dir` differing from `--git-common-dir`) and
  fails closed on a primary checkout, a plain directory, or a path whose only repo is an
  ancestor's. `ollama-worker.ps1 -Probe [-Cwd <path>]` runs that check and the rest of
  the preflight without launching anything, which is what `status`, `on` and the hook
  report from — one implementation, so a probe cannot promise what a dispatch refuses. The two values that reach that child's command line from outside the script
  (`-Model`, whether passed or read from state, and `-Resume`) are allowlisted - a model
  tag to `[A-Za-z0-9._:/-]`, a session id to `[A-Za-z0-9._-]`, neither with a leading
  dash - and the line itself is built by the CRT's own quoting rules, so neither can
  close an argument early and append flags of its own.
- **The forwarder calls the wrapper through the PowerShell tool**, and through Bash only
  when it has none. A worktree-isolated session (`EnterWorktree`, or an agent launched
  with worktree isolation) refuses any Bash command that starts `pwsh` — Claude Code's
  built-in check cannot show that a second shell will not run git — so a Bash-only
  forwarder could never dispatch from inside the worktree the wrapper requires. The
  PowerShell tool is not vetted that way and runs the same command line. A refusal means
  the wrapper never ran, so the caller re-issues the same command through its own
  PowerShell tool instead of recording the worker as unavailable.
- Every run appends one line to `~/.claude/ollama-workers.log.jsonl` (`event: "run"`,
  model, num_turns, duration_ms, escalate, reason). A probe that finds the directory
  not dispatchable while workers are on appends an `event: "probe"` row, so the log
  distinguishes "the worker was never usable in this repo" from "no task was a good
  fit" — a task that is never dispatched writes nothing otherwise. Calibrate `maxTurns`
  and the routing rubric from the run rows rather than from published benchmarks.
### advisor-bridge
- **The inverse of [`ollama-workers`](./ollama-workers).** That package spawns a child
  *away* from Anthropic; this one spawns a child *back to* it. Same reason in both
  cases: one Claude Code process serves exactly one endpoint, so reaching a second
  model means a second process.
- **Needs** Windows with PowerShell 7 (`pwsh`), the `claude` CLI on PATH with an
  Anthropic login, and Python 3 for the SessionStart hook. Run
  `./advisor-bridge/install.ps1` (`-DryRun` first) — a plain `cp -r` installs the skill
  but not the engine script, the persona, the config seed, or the hook.
- **Off by default, and invisible when off.** State lives in
  `~/.claude/advisor-bridge.json` seeded `enabled: false`. The hook reads that config
  *before* it checks the backend, so a disabled install costs no context; and the
  wrapper enforces the same gate itself, exiting 1 before launching anything, so a call
  on stale context after a compact cannot spend money.
- **Two fail-closed guards.** Before spawning, the child's environment key set must
  equal the whitelist exactly — not merely lack `ANTHROPIC_*`, which
  `CLAUDE_CODE_SUBAGENT_MODEL` walks straight through. After the call, the configured
  model must appear in the envelope's `modelUsage` and show it actually produced
  output; any other key present must itself be Claude Code's own `claude-haiku-*`
  housekeeping call (a real, correctly-routed reply carries one of those alongside the
  configured model) — anything else discards the reply and exits 2. Without the second
  guard the failure this package exists to prevent — the local model answering in the
  advisor's voice — returns silently, formatted as advice.
- **Costs $0.20–0.40 per call, every call.** Prompt caching matches an exact prefix and
  the rendered transcript is one user message that differs every time, so only the
  ~1.4 K persona amortizes. Read the `cost_usd` column of
  `~/.claude/advisor-bridge.log.jsonl` rather than that estimate; rows carrying
  `"source": "envelope-file"` are canned test runs and cost nothing.
- **Tests:** `Invoke-Pester advisor-bridge/tests -Output Detailed` (needs Pester 5;
  the Windows-bundled Pester 3 will not run them). Offline, deterministic, spends
  nothing. The one paid end-to-end check is a documented manual procedure at
  `advisor-bridge/tests/manual/e2e.md`, deliberately not a `*.Tests.ps1` file so no
  automated glob or CI gate can bill it.
- Invoke `/advisor-bridge on` once, then call it from an Ollama-backed session.
