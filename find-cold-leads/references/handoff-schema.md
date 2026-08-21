# Handoff Schema — find-cold-leads → climatepoint-contact-intelligence

The skill produces qualified named contacts via the **Apollo people pipeline**
(`apollo_mixed_people_api_search` → Stage Q → `apollo_people_bulk_match`) and
maps them to the `climatepoint-contact-intelligence` classifier's columns.

- **Primary (Apollo MCP):** the agent runs the pipeline via MCP tools and maps
  the enriched result to the classifier's columns (below).
- **Legacy (open web):** `scripts/lead_crawler.py` was removed when the skill
  moved to the Apollo pipeline; there is no crawler on disk to fall back to or
  inspect. Its `LEAD_COLUMNS` schema is preserved here for reference only, in
  case old workbooks produced before the switch are fed to the classifier.

## Legacy Mode O workbook (`LEAD_COLUMNS`) — reference only

The **Leads** sheet carried, in order: `company_name`, `domain`, `website`,
`country`, `region`, `sector`, `theme`, `matched_signal`, `target_persona`,
`contact_name`, `contact_title`, `contact_email`, `contact_page`,
`contact_link`, `contact_source_url`, `contact_confidence`, `contact_data_type`,
`person_source_type`, `public_profile_url`, `email_discovery_method`,
`email_verification_status`, `email_confidence`, `do_not_contact_reason`,
`linkedin_reference_url`, `lead_score`, `source_url`, `evidence_snippet`,
`business_relevance_basis`, `consent_status`, `outreach_allowed_review`,
`legitimate_interest_basis`, `delete_if_not_used_by`, `notes`, `odoo_ready`.
Plus sheets: **Sources**, **Rejected**, **Run Config**.

To feed the classifier, save the Leads sheet to CSV and map columns
(`company_name`→Company, `website`/`domain`→Website/Domain, `contact_name`→Name,
`contact_title`→Title, `contact_email`→Email, `country`→Country / HQ).

## Apollo `apollo_people_bulk_match` → classifier column map

The agent applies this mapping (Apollo enrichment is MCP/agent-driven; there
is no helper script — the mapping is done inline by the agent):

| Classifier column | Apollo field |
|---|---|
| `Email` | `email` |
| `Domain` | **email's domain** (fallback `organization.primary_domain`) |
| `Website` | `organization.website_url` |
| `Company` / `Company Name` | `organization.name` |
| `LinkedIn` / `linkedin_reference_url` | `linkedin_url` |
| `Title` / `Headline` / `Seniority` | `title` / `headline` / `seniority` |
| `Industry` / `Company Size` | `organization.industry` / `estimated_num_employees` |
| `Country / HQ` | `country` (refines the search-time region) |
| `Summary` | `organization.short_description` |
| `email_verification_status` | `email_status` (`none` if no email returned) |

### The two-domains rule (do not collapse these)

`email` domain and `organization.primary_domain` can differ (`sun-garden.de`
email vs `sun-garden.eu` org):

- **Outreach `Domain` + CAN-SPAM sender-ID** → the **email's** domain.
- **Company identity (`Website`), dedup (eTLD+1), blocklist matching** →
  `organization.primary_domain` / the resolved registrable domain.

### Credit accounting

`apollo_people_bulk_match`: 1 credit per matched person, 0 per no-match, max 10
per request. (Single `apollo_people_match` is the same unit cost for one person.)
No-match rows cost nothing. Track the per-row spend so the Run Config total
reconciles against a post-run `apollo_usage_stats_credit_usage_stats` delta.
Waterfall and phone reveal are off (`run_waterfall_email/phone=false`,
`reveal_phone_number=false`) — do not enable them unless the user explicitly
opts in and waterfall capability is confirmed first.

## Running the classifier afterwards

Convert/export the qualified rows to the classifier's input CSV, then:

```powershell
python <classifier>/climatepoint_classifier.py `
  --input  ".\outputs\dpp-de.csv" `
  --output ".\outputs\dpp-de-classified.csv" --resume
```

The classifier appends persona / lead-score / need-state / opportunity /
outreach-angle. Keep `outreach_allowed_review` at `needs review` until a human
confirms the basis.
