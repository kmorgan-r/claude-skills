# Handoff Schema — find-cold-leads → climatepoint-contact-intelligence

The output CSV is engineered to be the classifier's **input**. Get a column name
wrong and the handoff silently breaks (the classifier re-researches the field or
drops it). The canonical column list lives in `scripts/lead_crawler.py`
(`LEADS_COLUMNS`); this file explains the mapping and the rules.

## Column groups

1. **Identity** — `Name`, `First Name`, `Last Name`, `Email`. The classifier reads
   `Email` for identity even though it is not in its `ensure_headers`; emit it.
2. **Classifier columns** — the exact `ensure_headers` set (`Domain`, `Title`,
   `Company`, `Company Name`, `Website`, `LinkedIn`, `Industry`, `Company Size`,
   `Country / HQ`, `Summary`, `Headline`, plus the scoring columns it fills). We
   pre-fill the firmographic ones; the classifier's `set_if_empty` preserves them
   and only adds persona / lead-score / need / opportunity / outreach.
3. **Skill columns** — `qualification_tier`, `intent_signal`,
   `business_relevance_basis`, `evidence_snippet`, `source_url`, `source_mode`,
   `contact_source`, `region`, compliance fields, `email_verification_status`,
   `linkedin_reference_url`, `apollo_person_id`, `apollo_credits_consumed`. The
   classifier ignores these; they keep the lead auditable.

## Apollo `apollo_people_match` → column map

`map_apollo_person()` implements this. Key fields:

| Column | Apollo field |
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
| `apollo_person_id` | `id` (carry from free search → enrich) |

## The two-domains rule (do not collapse these)

`email` domain and `organization.primary_domain` can differ
(`sun-garden.de` email vs `sun-garden.eu` org). Use the right one per purpose:

- **Outreach `Domain` + CAN-SPAM sender-ID** → the **email's** domain.
- **Company identity (`Website`), dedup (eTLD+1), blocklist matching** →
  `organization.primary_domain` / the resolved company registrable domain.

## Credit accounting

`apollo_credits_consumed` is **1 only when the enrich returned a person with an
email**, else 0 (a no-match costs nothing). Sum the column for the Run Config
total; optionally reconcile against a post-run `apollo_usage_stats` delta.

## Running the classifier afterwards

```powershell
python <classifier>/climatepoint_classifier.py `
  --input  ".\outputs\dpp-de.csv" `
  --output ".\outputs\dpp-de-classified.csv" --resume
```

The classifier appends persona / lead-score / need-state / opportunity /
outreach-angle / next-action. `--resume` skips rows already classified.
