# Handoff Schema — find-cold-leads → climatepoint-contact-intelligence

The skill produces qualified leads two ways, both feeding the
`climatepoint-contact-intelligence` classifier:

- **Mode O (open web):** `scripts/lead_crawler.py` writes an **XLSX workbook**.
  Its column schema is the `LEAD_COLUMNS` constant in the script (the single
  source of truth — the creation path and the export read the same list).
- **Mode A (Apollo MCP):** the agent enriches qualified rows with
  `apollo_people_match` and maps the result to the classifier's columns (below).

## Mode O workbook (`LEAD_COLUMNS`)

The **Leads** sheet carries, in order: `company_name`, `domain`, `website`,
`country`, `region`, `sector`, `theme`, `matched_signal`, `target_persona`,
`contact_name`, `contact_title`, `contact_email`, `contact_page`,
`contact_link`, `contact_source_url`, `contact_confidence`, `contact_data_type`,
`person_source_type`, `public_profile_url`, `email_discovery_method`,
`email_verification_status`, `email_confidence`, `do_not_contact_reason`,
`linkedin_reference_url`, `lead_score`, `source_url`, `evidence_snippet`,
`business_relevance_basis`, `consent_status`, `outreach_allowed_review`,
`legitimate_interest_basis`, `delete_if_not_used_by`, `notes`, `odoo_ready`,
`odoo_duplicate`, `odoo_duplicate_status`, `odoo_duplicate_model`,
`odoo_duplicate_id`, `odoo_duplicate_reason`, `odoo_import_eligible`.
Plus sheets: **Sources**, **Rejected**, **Run Config**.

The Odoo duplicate fields are agent-annotated. `odoo_duplicate_status=not_screened`
means no completed Odoo screen has been recorded; `clear` means the read-only
screen found no match; `duplicate`, `possible_duplicate`, `blacklisted`, and
`screen_error` block or pause import through `odoo_import_eligible`.

To feed the classifier, save the Leads sheet to CSV and map columns
(`company_name`→Company, `website`/`domain`→Website/Domain, `contact_name`→Name,
`contact_title`→Title, `contact_email`→Email, `country`→Country / HQ).

## Mode A Apollo `apollo_people_match` → classifier column map

The agent applies this mapping (there is no `map_apollo_person` helper in the
script — Apollo enrichment is MCP/agent-driven):

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

A matched person costs 1 credit; a no-match costs 0. Track the per-row spend so
the Run Config total reconciles against a post-run `apollo_usage_stats` delta.

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
