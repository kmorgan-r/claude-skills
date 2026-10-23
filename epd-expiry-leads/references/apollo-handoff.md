# Apollo handoff

How to get Apollo working for this skill, and the two settings that change
the result. Written after the first full run on 2026-10-23.

## Getting Apollo

The Apollo MCP is registered per-project in `~/.claude.json`, and in the
`claude-skills` project it is not registered at all. So the `find-cold-leads`
skill cannot reach Apollo from here as-is. Two ways forward.

**Add it to the project scope.** Copy the server block from
`projects["C:/Users/kmorg"].mcpServers` in `~/.claude.json` into
`projects["C:/Users/kmorg/claude-skills"].mcpServers` and restart the
session:

```json
"apollo": { "type": "http", "url": "https://mcp.apollo.io/mcp" }
```

**Or drive it over JSON-RPC.** The server is stateless: no session header,
no `initialize` handshake needed, `tools/call` works on its own. The three
tools this pipeline needs are `apollo_mixed_people_api_search`,
`apollo_people_bulk_match` and `apollo_usage_stats_credit_usage_stats`.

### The token

Apollo uses OAuth, so there is no API key to store. The credentials live in
`~/.claude/.credentials.json` under `mcpOAuth["apollo|<hash>"]`. The access
token expires, and the refresh token is only accepted at one endpoint:

```
POST https://mcp.apollo.io/api/v1/oauth/token
grant_type=refresh_token&refresh_token=...&client_id=...&redirect_uri=...
```

That path comes from the server's own metadata at
`https://mcp.apollo.io/.well-known/oauth-authorization-server`. The shorter
`https://mcp.apollo.io/token` path is not the token endpoint: it answers 403
from Cloudflare and then 401.

Write the returned `access_token` and `refresh_token` back into
`.credentials.json` and set `expiresAt` to now plus 2592000 seconds, so the
next session does not have to do this again.

## Search one domain at a time

Batch the domains and the search results come back with no organization
domain, so there is no way to tell which company a person belongs to.
`organization` carries a name and a set of `has_*` flags but no domain.

One domain per call is free and the domain is implied, so the per-company
cap and the lead-list join both work. It costs 23 calls instead of 2.

## Rank by the lead score first

The first run ranked enrichment candidates on title seniority alone and
enriched 25 people of whom a third were off-ICP: a Head of Marketing, an
Accruals and Compliance Program Manager, a Junior QC Manager, a Regional
Procurement Manager. The credits were spent and the rows were not usable.

Rank by the EPD lead score first, then `has_email`, then title seniority.

The lead score is the right prior because it encodes something Apollo
cannot see. A company whose EPD was prepared in-house has an LCA team, and
the renewal conversation runs through that team. A company whose EPD was
written by a consultant does not, and the consultant is the one who will
renew it. Score 4 outranks score 0 even when the score-0 person has the
better title.

Then cap at 3 per company so one large firm does not take the whole budget.

## Filter on the role, not the word

A title filter needs an order, because several titles contain both an
off-ICP word and a relevant one:

- "Accruals and Compliance Program Manager" is an accounting role.
- "Director, Marketing & Product Sustainability" owns product
  sustainability.
- "Global Sustainability Director" owns it, and the word "Sustainability"
  does not end at a word boundary, so `\bsustainab\b` never matches it.

Check a hard off-ICP list first (accounting, legal, junior, intern,
coordinator, assistant, associate, supply chain, warehouse, payroll,
treasury, tax), then a relevant-remit list (sustainability, ESG,
environmental, EHS, carbon, product stewardship, life cycle, LCA,
compliance), then a broader one (quality, QA, QC, product data, product
line, product development, product manager, procurement, technical,
engineering).

## Filter the region, or do not claim one

`source-compliance.md` says to tag region at search time, from the
`person_locations` filter, not from the enrich `country` - because most
rows are never enriched. If the search was not location-filtered, the
region is unknown for every row and the honest posture is the conservative
EU one.

The first run searched by company domain only, with no location filter, and
then described the list as US-only. That was wrong: 7 of the 50 enriched
people were outside the US (Italy, UK, China, Australia, Belgium). Filtering
by *company* country says nothing about the *person's* country, and a
contact in Varese working for a US company is still an EU contact under
GDPR and Italian ePrivacy.

Two options, and pick one deliberately:

- Filter the search with `person_locations` and lose the non-US people
  entirely, which is right if the outreach is US-only.
- Do not filter, and split the export by the enrich country: US contacts in
  the Leads sheet, everyone else in a review-gated sheet, with the region
  and the posture on every row.

Either way, do not write "US only" in the run summary unless the search
actually filtered on location.

## Check the company, not just the person

An email-level suppression check does not catch a company that is already
in the CRM under a different contact. Search the 23 domains by company
domain and by company name across `res.partner`, `crm.lead` and
`mailing.contact` as well, and say in the summary whether any of them is
already known.

## The Odoo suppression check

Before exporting, check every business email against `mail.blacklist`,
`mailing.contact`, `res.partner` and `crm.lead`. A row that hits any
source is not importable, whatever the duplicate check says.

Use `ilike` on the email rather than `in` on an exact list. `in` with a
list of addresses misses a row where the address differs in case, and it
misses `crm.lead` rows whose `email_from` holds a display-name form. And
do not filter on `active` - an archived `mailing.contact` that opted out
still suppresses. The 59 business emails in this run were checked with
`ilike` across all four models with no active filter, and 0 hit.

The `climatepoint-odoo` MCP's `search_read` tool cannot take a domain with
a leaf condition - it fails with `unhashable type: 'dict'` on any
`[["field","op",value]]` and only an empty domain works. Call Odoo's
JSON-RPC endpoint directly instead. Credentials are already in the
environment as `ODOO_LOGIN` and `ODOO_API_KEY`, the same ones the MCP
server uses. Send a browser `User-Agent` or Cloudflare answers 403.

Field names differ per model: `mail.blacklist` has `email` and no
`email_normalized` or `is_blacklisted`, `mailing.contact` has no `active`,
and `crm.lead` has no `company_name`. Passing an unknown field is a hard
error, so use a per-model field list.

A runnable client that does all of this is in the run scripts under
`reports/run-scripts/` after a run. It is a run artifact, not part of the
skill, and it is gitignored.
