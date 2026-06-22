# Odoo field reference — linkedin-outreach-odoo

The skill reads and writes `mailing.contact` in the local Odoo DB (`ClimatePoint`)
through the **climatepoint-odoo MCP** (registered as `mcpServers.climatepoint-odoo`
in `C:\Users\kmorg\.claude.json`). No `odoo shell`, no XML-RPC config — Claude calls
the MCP tools directly.

This table is **verified against the live DB** (June 2026). If a future
`fields_get` differs, trust the live result.

## Verified `mailing.contact` schema (fields the skill uses)

| Purpose | Real field | Type | Notes |
|---|---|---|---|
| Person's LinkedIn URL | `x_linkedin_url` | char | eligibility filter + send target |
| **Outreach state** (the contacted tracker) | `x_lead_status` | char | `New`, `Attempting contact`, `Connected`, `Closed`, or unset |
| First name | `first_name` | char | |
| Last name | `last_name` | char | |
| Job title | `x_job_title` | char | |
| Headline (richer, optional) | `x_headline` | char | fallback for headline |
| Company | `company_name` | char | NOT `x_company` |
| Country | `country_id` | many2one | returned as `[id, "Name"]`; use the name |
| Pre-written pitch | `x_outreach_angle` | text | per-lead angle from `/find-cold-leads` — best note seed |
| Need state | `x_need_state` | char | e.g. "Scope 3 / Supplier Footprint" |
| Persona | `x_persona` | selection | investor / sustainability / … — filter target |
| Lead score | `x_lead_score` | integer | order desc to prioritize |
| Email | `email` | char | context only, not used to send |

Other useful real fields seen on the model: `x_seniority`, `x_department_function`,
`x_opportunity_type`, `x_next_action`, `x_recommended_offer`, `x_company_size_id`,
`x_regulatory_exposure_ids`, `x_industry`, `x_lead_score`.

## The contacted tracker is `x_lead_status` — no new fields needed

Earlier drafts assumed `x_linkedin_contacted` / `_date` / `_detail` fields would
be created. **They were never needed.** `x_lead_status` already encodes outreach
state. As of the verification snapshot, leads carrying a LinkedIn URL break down:

| `x_lead_status` | count | meaning | eligible? |
|---|---|---|---|
| `New` | 243 | not yet contacted | ✅ |
| unset (`false`) | 113 | not yet contacted | ✅ |
| `Attempting contact` | 1825 | request already sent | ❌ |
| `Connected` | 984 | accepted | ❌ |
| `Closed` | 5 | done | ❌ |

State machine this skill drives: **`New`/unset → (send connect) → `Attempting
contact`**. Acceptance (`Connected`) and closing happen elsewhere. So:
- **Export eligibility:** `x_lead_status` in (`New`, unset).
- **Write-back after send:** set `x_lead_status = "Attempting contact"`.

If you ever DO want LinkedIn-specific bookkeeping fields (send timestamp, raw rate
header) on the contact, add them via Odoo Studio or a tiny `_inherit` module
mirroring `cp_mailing_drip/models/mailing_mailing.py`. Not required for the core
flow — the outreach log CSV already holds that detail locally.

## Populating LinkedIn URLs when importing leads

`/find-cold-leads` writes an XLSX whose `linkedin_reference_url` column holds the
person's LinkedIn URL for Apollo-enriched rows. When importing the Leads sheet
into `mailing.contact`, map:

- `linkedin_reference_url` → `x_linkedin_url`
- `contact_name` → split into `first_name` / `last_name`
- `contact_title` → `x_job_title`
- `company_name` → `company_name`
- `contact_email` → `email`
- `country` → `country_id` (Odoo resolves the m2o by name)
- `matched_signal` / pitch → `x_outreach_angle`

Set `x_lead_status = "New"` (or leave unset) on fresh imports so they're eligible.
Only import rows the user reviewed and marked `odoo_ready=yes` in the workbook.

**Data caveat:** Apollo enrichment sometimes mis-tags `country_id` (e.g. a US or
French fund shows "Gabon"). Treat `country_id` as low-confidence note color, not a
filter.

## Odoo access via MCP (replaces odoo shell)

- **`search_read`** (read-only, immediate) — export eligible leads:
  ```
  search_read:
    model: "mailing.contact"
    domain: [["x_linkedin_url","!=",false], ["x_linkedin_url","!=",""],
             "|", ["x_lead_status","=","New"], ["x_lead_status","=",false]]
    fields: ["id","first_name","last_name","x_job_title","x_headline",
             "company_name","x_linkedin_url","email","country_id",
             "x_outreach_angle","x_need_state","x_persona","x_lead_score"]
    limit: 25
    order: "x_lead_score desc, id asc"
  ```
  Max `limit` is 200. `false` = Odoo False/unset.
- **`execute_action`** with `method: "fields_get"` (read-only, immediate) — schema
  discovery. Also `method: "read_group"` with `groupby: ["x_lead_status"]` to count
  the eligible pool.
- **`write`** (two-step confirmation) — flip state after send. See SKILL.md step 7.
  Call without `confirmation_code` → get 6-char code → show user → call again with
  `confirmation_code`. Max 100 ids/call; one `values` dict applied to all ids:
  ```
  write:
    model: "mailing.contact"
    ids: [<sent odoo_ids>]
    values: { "x_lead_status": "Attempting contact" }
  ```

No env vars per-run, no `odoo` binary, no shell piping. The MCP handles Odoo
JSON-RPC auth via `ODOO_LOGIN` + `ODOO_API_KEY`.