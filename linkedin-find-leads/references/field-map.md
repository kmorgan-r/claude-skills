# Odoo field reference — linkedin-find-leads

The skill writes `mailing.contact` in the local Odoo DB (`ClimatePoint`)
through the **climatepoint-odoo MCP** (registered as `mcpServers.climatepoint-odoo`
in `~\.claude.json`). All Odoo access is agent-side via MCP tools — no `odoo shell`,
no XML-RPC.

This table is **verified against the live DB** (2026-06-29) via `fields_get`.
Re-verify at first use: the script's `verify_schema` fails fast on any missing
required field, and `fields_get` is re-run at first use to catch schema drift.

## Verified `mailing.contact` field map (fields this skill writes)

| Odoo field | Type | Source | Notes |
|---|---|---|---|
| `x_linkedin_url` | char | search item `profileUrl` | native key, always present |
| `first_name` | char | search item | |
| `last_name` | char | search item | |
| `x_headline` | char | search item `headline` | untrusted free text |
| `x_job_title` | char | search `currentPosition` (fallback: enrich `experience[0].title`) | no top-level `title` in `get_profile` — do not read it |
| `company_name` | char | enrich `currentCompany` | NOT `x_company` |
| `x_summary` | text | enrich `aboutText` | untrusted free text; field key is `aboutText`, not `about` |
| `x_seniority` | selection (5 keys) | classifier-derived from title | **no `unknown` key** — unmappable → **omit field** from create payload; write key not label |
| `x_persona` | selection (10 keys) | classifier | **must be a fixed key**; invalid → coerce to `unknown` (a valid key); write key not label |
| `x_need_state` | char | classifier | free text |
| `x_lead_score` | integer (1–10) | classifier | emit an int, not a string |
| `x_outreach_angle` | text | classifier | untrusted free text |
| `x_lead_status` | char | literal `"New"` | makes the lead eligible for /linkedin-outreach-odoo |
| `email` | char | blank `""` | LinkedIn-only; Apollo email enrichment is a later pass |
| `country_id` | many2one | resolve `location` → `res.country` id | best-effort; **omit on no-match, never write `false`** |
| `x_industry` | char | classifier-derived (optional) | `get_profile` returns neither `x_industry` nor `x_department_function` — do not read from enrich; may be left blank in v1 |
| `x_department_function` | char | classifier-derived (optional) | same caveat as `x_industry` |

## Selection-field key sets (write the KEY, never the label)

Selection fields validated by the script's `coerce_persona` / `validate_seniority`
and by `build_payload` before any MCP `create`. The MCP will reject an unrecognized
key — do not guess or infer a key from a label.

### `x_persona` — 10 valid keys

```
sustainability
product_rd
ops_sc
founder_exec
investor
marketing
technical
partner
low_fit
unknown
```

Invalid or absent persona → coerce to `unknown`. The `unknown` key exists specifically
as the per-row fallback; it is a valid key and must never be omitted.

### `x_seniority` — 5 valid keys

```
analyst
manager
director
vp
c_level
```

Note: the label for `director` is "Director / Head" and the label for `vp` is
"VP / Director" — write the key, not the label. **There is no `unknown` seniority
key.** An unmappable title must leave `x_seniority` **unset** (omit from the create
payload) and set the workbook flag `seniority_unset=yes` on that row. Never write an
invalid key, never abort the batch.

## `country_id` caveat

`country_id` is a many2one field. When unset, Odoo returns `false` (Python `False`),
not `None` or `""`. When building a location string from a read record use
`country_id[1] if country_id else ""` — a bare `country_id[1]` raises `TypeError`
when the field is `false`.

For write payloads: resolve the free-text `location` to a `res.country` id
(best-effort). On no-match, **omit `country_id` from the create payload entirely**
— never write `false`, never write `0`. This rule is enforced in the script's
`build_payload` and must be mirrored in the agent's MCP create step.

## Schema-manifest / verify_schema discipline

Before any sourcing or enrich spend, the agent:

1. Calls MCP `execute_action` → `fields_get` on `mailing.contact` to get the live
   field set.
2. Writes the present field names (keys only) to
   `%TEMP%\linkedin-find-leads\schema-manifest.json` as a JSON array.
3. Passes `--schema-manifest %TEMP%\linkedin-find-leads\schema-manifest.json` to
   the script.

The script's `verify_schema` then fails fast — before sourcing or spending any
enrich budget — if any required field from `REQUIRED_FIELDS` is absent from the
manifest. A clear error names the missing field(s).

This ensures schema drift surfaces immediately, not as a silent bad `create` on
the final MCP write step.

## MCP access pattern

- **`execute_action` → `fields_get`** (read-only, immediate) — schema discovery.
- **`search_read`** (read-only, immediate) — exclude-set build and pre-create re-query.
  Max `limit` 200 per page; page until all records retrieved.
- **`create`** (two-step confirmation) — gated by a server-generated 6-char code.
  Call without `confirmation_code` → get code → show user → call again with
  `confirmation_code`. Never fabricate the code. Create row-by-row, one record per
  call, writing `created=yes` + the new id back to the workbook on each success.
