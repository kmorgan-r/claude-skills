# Claude Code Skills

A personal collection of [Claude Code](https://claude.com/claude-code) skills.

Each subdirectory is a self-contained skill: a `SKILL.md` (the instructions Claude
follows at runtime) plus any bundled scripts, references, and evals.

| Skill | What it does |
|-------|--------------|
| [`find-cold-leads`](./find-cold-leads) | Finds and **qualifies** B2B cold leads on free signals, spends scarce Apollo enrichment credits only on rows that fit the ICP, tags each with a region-aware compliance posture, and exports a classifier-ready / Odoo-ready sheet. |
| [`linkedin-outreach-odoo`](./linkedin-outreach-odoo) | Picks up where `find-cold-leads` leaves off: reads eligible `mailing.contact` leads from Odoo, drafts a personalized LinkedIn connection note per lead, sends connection requests via the ConnectSafely API (dry-run by default), and writes outreach state back to Odoo. |
| [`fix-pr-reviews`](./fix-pr-reviews) | Fetches the most recent GitHub PR review comments and systematically addresses each one — no copy-pasting from the PR. Supports a `--loop` mode. |

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
- Invoke by asking to reach out to your Odoo cold leads on LinkedIn.

### fix-pr-reviews
- **Needs** the GitHub CLI (`gh`) authenticated.
- Invoke `/fix-pr-reviews` (optionally `--loop`) inside a repo with an open PR.
