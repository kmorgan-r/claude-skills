# Claude Code Skills

A personal collection of [Claude Code](https://claude.com/claude-code) skills.

Each subdirectory is a self-contained skill: a `SKILL.md` (the instructions Claude
follows at runtime) plus any bundled scripts, references, and evals.

| Skill | What it does |
|-------|--------------|
| [`find-cold-leads`](./find-cold-leads) | Finds and **qualifies** B2B cold leads on free signals, spends scarce Apollo enrichment credits only on rows that fit the ICP, tags each with a region-aware compliance posture, and exports a classifier-ready / Odoo-ready sheet. |
| [`linkedin-outreach-odoo`](./linkedin-outreach-odoo) | Picks up where `find-cold-leads` leaves off: reads eligible `mailing.contact` leads from Odoo, drafts a personalized LinkedIn connection note per lead, sends connection requests via the ConnectSafely API (dry-run by default), and writes outreach state back to Odoo. |
| [`fix-pr-reviews`](./fix-pr-reviews) | Fetches the most recent GitHub PR review comments and systematically addresses each one — no copy-pasting from the PR. Supports a `--loop` mode. |
| [`ship`](./ship) | Conductor that drives the post-brainstorm dev pipeline hands-off — spec-review, plan, plan-review, implementation, PR, then the `fix-pr-reviews` loop — resuming across `/clear` via a state file, stopping only on failure or final merge. |
| [`reviewing-plans`](./reviewing-plans) | Reviews a written implementation plan before execution: dispatches 2–5 domain-specific reviewer agents in parallel, consolidates findings, and applies approved fixes to the plan file. |

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

### find-cold-leads
- **Hands off to** a separate `climatepoint-contact-intelligence` classifier (the
  scorer) — **not included here**. Without it, the handoff still produces the
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
- Invoke `/ship` once; it runs phases P0–P7 hands-off.

### reviewing-plans
- Takes a path to an existing plan markdown file (or finds the most recently referenced
  one). Used standalone or as a `ship` phase.
- Invoke `/reviewing-plans` after a plan exists, before execution.
