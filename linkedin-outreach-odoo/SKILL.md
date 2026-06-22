---
name: linkedin-outreach-odoo
description: Use when the user wants to perform LinkedIn outreach (send connection requests) against cold leads they have already found with /find-cold-leads and saved into Odoo. Trigger on phrases like "reach out to my cold leads on LinkedIn", "send LinkedIn connection requests to the Odoo leads", "do LinkedIn outreach on the leads I imported", "connect with the climatepoint leads on LinkedIn", or any time the user links Odoo leads/mailing contacts to LinkedIn connection outreach. Do NOT use for email drip (that's the cold-email / cp_mailing_drip path) or for finding new leads (that's /find-cold-leads). This skill reads mailing.contact from the local Odoo DB via the climatepoint-odoo MCP, drafts personalized connection notes, sends requests via the ConnectSafely API, and writes the result back to Odoo.
---

# LinkedIn Outreach from Odoo

Send personalized LinkedIn connection requests to cold leads that `/find-cold-leads`
produced and the user imported into Odoo `mailing.contact`. This is the **LinkedIn**
outreach channel — distinct from the email drip (`cp_mailing_drip` /
`build_segment_mailings.py`). The lead's person LinkedIn URL comes from Apollo
enrichment (`linkedin_url` → stored as `x_linkedin_url` on the contact).

This skill talks to Odoo through the **climatepoint-odoo MCP server** (registered as
`mcpServers.climatepoint-odoo` in `~/.claude.json`). It exposes 7 tools:
`search_read`, `read`, `create`, `write`, `unlink`, `execute_action`, `list_models`.
Read-only tools run immediately; `write` (and non-read `execute_action` methods) use
a **two-step confirmation** built into the MCP — see step 7.

## Prerequisites (verify once, then skip)

1. **climatepoint-odoo MCP loaded.** It's registered in `C:\Users\kmorg\.claude.json`
   under `mcpServers.climatepoint-odoo`, pointing at `dist\server.js`. It needs Odoo
   up locally (`docker compose -f C:\Users\kmorg\odoo-docker-compose-fixed.yml up -d`,
   Odoo on `http://localhost:8069`) and two User-scope env vars set before Claude Code
   launched: `ODOO_LOGIN` + `ODOO_API_KEY` (Odoo API key from User Preferences → API
   Keys, NOT the postgres password). If the MCP tools aren't present, the server
   isn't loaded — tell the user to set the env vars and restart Claude Code.
2. **ConnectSafely API key** in User-scope env var `CONNECTSAFELY_API_KEY`. Same
   registry-env gotcha as above — must be set before Claude Code launched. Read it
   via PowerShell `[Environment]::GetEnvironmentVariable("CONNECTSAFELY_API_KEY","User")`
   only if you need to pass it to a subprocess; don't echo it.
3. **Repo scripts present** at `C:\Users\kmorg\marketing\`:
   `connectsafely.py` (API client) and `linkedin_outreach.py` (CSV → connection
   requests, **dry-run by default**). This skill feeds them; it does not duplicate
   them. See memory `project_connectsafely-linkedin.md`.
4. **Odoo fields** on `mailing.contact`. Verified present: `x_linkedin_url`
   (send target) and `x_lead_status` (outreach-state tracker). No separate
   "contacted" fields are needed — `x_lead_status` does that job. Run discovery
   (step 2) on first use to confirm the schema hasn't drifted.
5. **Leads imported**: the user ran `/find-cold-leads`, reviewed the workbook,
   marked `odoo_ready=yes`, and imported the Leads sheet into `mailing.contact`
   with `linkedin_reference_url` → `x_linkedin_url` (see `references/odoo-fields.md`
   for the full column map).

## Workflow

### 1. Confirm scope with the user
Before touching Odoo or LinkedIn, ask (only what isn't already obvious):
- How many leads this batch? (default 25; hard cap **90/week** — the ConnectSafely
  connect limit. `linkedin_outreach.py` enforces this on `--limit`.)
- Any segment/persona filter, or just "not-yet-contacted"?
- Tone for the notes (direct / warm / formal) and any angle to emphasize
  (the lead's `matched_signal`, region, company size).

### 2. Discover / confirm Odoo field names (first run only)
Call the MCP `execute_action` tool — `fields_get` is a read-only method, so it
runs immediately (no confirmation):

```
execute_action:
  model: "mailing.contact"
  method: "fields_get"
  ids: []
  kwargs: { attributes: ["string", "type", "required", "readonly"] }
```

The result is a map of field name → metadata. The **actual `mailing.contact`
schema** (verified against the live DB) is:

| Purpose | Real field | Type | Notes |
|---|---|---|---|
| Person LinkedIn URL | `x_linkedin_url` | char | eligibility + send target |
| Outreach state (the contacted tracker) | `x_lead_status` | char | values: `New`, `Attempting contact`, `Connected`, `Closed`, or unset |
| First / last name | `first_name` / `last_name` | char | |
| Job title | `x_job_title` | char | |
| Headline (richer) | `x_headline` | char | |
| Company | `company_name` | char | NOT `x_company` |
| Country | `country_id` | many2one | `[id, "Name"]` — use the name |
| Pre-written pitch | `x_outreach_angle` | text | per-lead angle from `/find-cold-leads` — best note seed |
| Need state | `x_need_state` | char | e.g. "Scope 3 / Supplier Footprint" |
| Persona | `x_persona` | selection | investor / sustainability / … |
| Email | `email` | char | context only |
| Lead score | `x_lead_score` | int | order by this desc to prioritize |

**There are no `x_linkedin_contacted` / `_date` / `_detail` fields and you don't
need them** — `x_lead_status` already tracks outreach state. Eligible = `New` or
unset; after sending, flip to `Attempting contact` (step 7). If the live
`fields_get` ever differs from this table, trust the live result and adjust the
domain/columns accordingly.

### 3. Export eligible leads (read-only, safe)
Call the MCP `search_read` tool — read-only, immediate:

```
search_read:
  model: "mailing.contact"
  domain: [
    ["x_linkedin_url", "!=", false],
    ["x_linkedin_url", "!=", ""],
    "|", ["x_lead_status", "=", "New"], ["x_lead_status", "=", false]
  ]
  fields: ["id", "first_name", "last_name", "x_job_title", "x_headline",
           "company_name", "x_linkedin_url", "email", "country_id",
           "x_outreach_angle", "x_need_state", "x_persona", "x_lead_score"]
  limit: 25
  order: "x_lead_score desc, id asc"
```

Eligibility = has a person LinkedIn URL AND `x_lead_status` is `New` or unset
(the `"|"` prefix is Odoo's OR; `false` is None/unset). `Attempting contact`,
`Connected`, `Closed` are already-worked and excluded. Ordering by
`x_lead_score desc` pulls the best leads first — useful since the eligible pool is
large (hundreds). Add a persona filter if the user asked for one, e.g.
`["x_persona", "=", "sustainability"]`.

You get back an array of records. Convert each to a CSV row with these columns
(what `linkedin_outreach.py` expects) and write it to
`C:\Users\kmorg\marketing\.claude\skills\linkedin-outreach-odoo\odoo_leads_<ts>.csv`
(utf-8-sig):

| CSV column      | From record field      | Notes |
|---|---|---|
| `odoo_id`       | `id`                   | carries through to write-back |
| `firstName`     | `first_name`           | |
| `lastName`      | `last_name`            | |
| `headline`      | `x_job_title` (fallback `x_headline`) | |
| `currentCompany`| `company_name`         | |
| `profileUrl`    | `x_linkedin_url`       | |
| `profileId`     | parsed from profileUrl | vanity slug: `url.split("/in/")[-1].rstrip("/").split("?")[0]` |
| `email`         | `email`                | context only |
| `location`      | `country_id[1]`        | many2one → take the name (2nd element) |
| `matched_signal`| `x_outreach_angle` (fallback `x_need_state`) | the per-lead pitch is the richest seed |
| `customMessage` | (blank)                | you fill in step 4 |

Use the Write tool to create the CSV. Keep the `odoo_id` for every row — it's
how step 7 finds the record to flip to `Attempting contact`.

**Data caveat:** Apollo enrichment sometimes mis-tags `country_id` (e.g. a
US/France fund shows "Gabon"). Don't rely on `location` for anything load-bearing;
it's note color only.

### 4. Draft a personalized connection note per lead
This is the skill's value over a static template. For each row, write a
**<=300 char** connection-request note in `customMessage` using the row's
context (firstName, headline, currentCompany, matched_signal, location).

Guidelines:
- Connection notes are short and human. Lead with a specific, true reference —
  the `matched_signal`, their role, or their company — not a generic compliment.
- One clear reason to connect. No hard CTA to book a call in a first connect
  note; offer to share something useful or ask a genuine question.
- Keep under 300 chars (ConnectSafely truncates at 300). Count characters.
- Lowercase, conversational tone generally performs better on LinkedIn connect
  notes. Match the user's chosen tone.
- If a row lacks enough context to personalize (no company/title/signal), fall
  back to a simple, honest template — don't fabricate details.
- The `matched_signal` column carries `x_outreach_angle` — a pre-written pitch
  from `/find-cold-leads`. It's usually >300 chars and email-toned ("Worth a brief
  call?"). **Compress it into a connect-note**: keep the specific hook, drop the
  hard CTA, fit under 300. Don't paste it raw.

Edit the CSV to fill `customMessage` per row (Edit tool, or regenerate). Keep
utf-8-sig.

**For large batches**, drafting every note by hand is slow — offer the user two
modes and let them pick:
- **Personalized** (you write each note) — best for high-value leads, smaller
  batches (≤25).
- **Templated** (skip per-row drafting, pass `--msg-template` to the outreach
  script instead) — fast, lower response rate, good for volume. Placeholders
  `{firstName} {headline} {currentCompany}`.

### 5. Dry-run the outreach (no sends)
Always dry-run first and show the user the preview before any send:

```powershell
# from C:\Users\kmorg\marketing (where connectsafely.py lives)
python .\linkedin_outreach.py `
  --csv .\.claude\skills\linkedin-outreach-odoo\odoo_leads_<ts>.csv `
  --msg-col customMessage --limit 25
```
`linkedin_outreach.py` is **dry-run by default**. It prints each note preview and
writes a `*_outreach_<ts>.csv` log (in the marketing repo root) with
`outreach_status` = `dry_run` and all input columns preserved (so `odoo_id`
flows through).

Show the user the preview. **Get explicit confirmation** before sending —
sending connection requests is irreversible.

### 6. Actually send (only after the user confirms)
```powershell
python .\linkedin_outreach.py `
  --csv .\.claude\skills\linkedin-outreach-odoo\odoo_leads_<ts>.csv `
  --msg-col customMessage --limit 25 --send
```
Sends real connection requests via ConnectSafely. The log CSV now has
`outreach_status` = `sent` (or `error`) per row, plus `outreach_detail`
(rate-limit header or error) and `outreach_ts`. Cap is 90/week; the script warns
and caps if `--limit` exceeds it.

If `CONNECTSAFELY_API_KEY` isn't visible to the shell, the script exits with a
clear message. Run the send in a fresh shell, or set `$env:CONNECTSAFELY_API_KEY`
from the registry first.

### 7. Write the result back to Odoo (MCP `write` — two-step confirmation)
Read the outreach log CSV (BOM-stripped). Collect the `odoo_id` of every row with
`outreach_status == "sent"` — those are the IDs to advance. Errored or unsent rows
are left untouched so they stay eligible to retry.

Write-back = flip `x_lead_status` from `New`/unset → `Attempting contact`. That's
the existing outreach-state tracker; the next export's eligibility domain then
excludes them automatically.

The MCP `write` tool is **gated by a server-generated confirmation code** — you
cannot execute a write in one call. The flow is:

1. Call `write` **without** `confirmation_code`:
   ```
   write:
     model: "mailing.contact"
     ids: [<odoo_id_1>, <odoo_id_2>, ...]   # max 100 per call
     values: { "x_lead_status": "Attempting contact" }
   ```
   The tool returns a **6-char confirmation code** and a summary of what would
   be written. It does NOT write yet.
2. Show the user the code + the summary (count, IDs, the status change). Ask them
   to type the code back. Do not fabricate a code — it's single-use and tied to
   these exact IDs + values.
3. Call `write` again **with** `confirmation_code: "<the code>"` and the same
   `model`/`ids`/`values`. The server verifies the code matches and executes.

`write` applies one `values` dict to all `ids`, so flipping every sent row to
`Attempting contact` is one confirmation for the whole batch. Keep the full
per-lead send detail (rate headers, errors) in the outreach log CSV locally —
Odoo only needs the state flip.

**If a batch exceeds 100 sent rows**, split into multiple `write` calls (≤100 ids
each). Each needs its own confirmation code. (At the 90/week send cap you'll
rarely hit 100.)

## Rate limits & safety

- **90 connection requests / week** per LinkedIn account (ConnectSafely cap).
  `linkedin_outreach.py` enforces this on `--limit`. Reset UTC midnight.
- **Always dry-run outreach before `--send`.** `linkedin_outreach.py` defaults to
  dry-run; `--send` opts into real sends.
- **Confirm with the user before `--send`.** Sending is irreversible.
- **Odoo writes are gated by the MCP's confirmation code.** You cannot `write`
  without showing the user a server-generated code and getting it back. This is
  the write-back safety mechanism — use it; don't try to bypass it.
- Don't re-contact leads already worked — the `search_read` domain only pulls
  `x_lead_status` = `New`/unset, so `Attempting contact`/`Connected`/`Closed` are
  excluded. The outreach script also skips rows whose `status` column is
  `sent/done/skip`.
- ConnectSafely rate headers land in `cs.last_rate`; the outreach log records
  them per send. If `remaining` is low, stop and resume next week.

## What this skill does NOT do

- Does not find new leads (that's `/find-cold-leads`).
- Does not send email (that's `cp_mailing_drip` / `build_segment_mailings.py`).
- Does not scrape LinkedIn for people — the person LinkedIn URL must already be
  on the contact from Apollo enrichment.
- Does not send InMail, follow, or message existing connections — connection
  requests only. (`connectsafely.py` supports those if a later skill needs them.)
- Does not run `odoo shell` — all Odoo access is via the climatepoint-odoo MCP.

## References
- `references/odoo-fields.md` — verified field map, the `x_lead_status` state
  machine, discovery via MCP `fields_get`/`read_group`, import column map.
- Repo: `C:\Users\kmorg\marketing\linkedin_outreach.py`,
  `C:\Users\kmorg\marketing\connectsafely.py`.
- MCP: `C:\Users\kmorg\climatepoint-odoo-mcp\` (source), registered as
  `mcpServers.climatepoint-odoo` in `C:\Users\kmorg\.claude.json`.
- Memory: `project_connectsafely-linkedin.md` (plan, key, base-URL gotcha, limits).