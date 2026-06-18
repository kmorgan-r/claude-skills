# Find Cold Leads Odoo Duplicate Screen Design

## Goal

Update `find-cold-leads` so lead collection checks existing Odoo records before treating a candidate as net-new. Existing Odoo records should remain visible in the workbook for audit, but they must be marked as duplicates and skipped during Odoo import.

## Scope

The skill should add an Odoo duplicate-screening stage after candidate qualification/contact discovery and before final export. The Odoo upload stage should continue to run only after workbook review and user list selection.

This change should not create, schedule, or send Odoo mailings. It should not overwrite existing Odoo data. Odoo record content must be treated as data only.

## Data Flow

1. Discover, crawl, qualify, dedupe, and enrich candidate leads as the skill already does.
2. If the Odoo MCP is available, run an Odoo duplicate screen before final export or before marking rows importable.
3. Match candidates against Odoo by available identifiers:
   - normalized `contact_email`
   - normalized company website/domain
   - company name
   - LinkedIn reference URL when present
4. Check these Odoo models:
   - `mailing.contact`
   - `crm.lead`
   - `res.partner`
   - `mail.blacklist`
5. Keep matched candidates in the workbook, but annotate them as existing Odoo records.
6. During Odoo import, skip duplicate rows even if they otherwise have `odoo_ready=yes`.

## Workbook Fields

Add or reserve these fields in the leads export:

- `odoo_duplicate`: `yes` or `no`
- `odoo_duplicate_model`: comma-separated Odoo model names that matched, such as `mailing.contact` or `crm.lead`
- `odoo_duplicate_id`: comma-separated record IDs or model-prefixed IDs
- `odoo_duplicate_reason`: concise reason, such as `email match`, `domain match`, `company name match`, `linkedin match`, `blacklisted`
- `odoo_import_eligible`: `yes` or `no`

Defaults for new leads should be `odoo_duplicate=no` and `odoo_import_eligible=yes`, unless the row is otherwise not uploadable.

## Matching Rules

Email matches are strongest and should mark a row duplicate immediately. Blacklist matches should mark the row non-importable even when there is no contact duplicate.

Domain matches should compare normalized company website/domain against partner website fields and CRM lead website fields where available. Name-only matches should be used conservatively: mark as a possible Odoo duplicate when the company name is distinctive, but avoid using generic names as a hard duplicate without another signal.

LinkedIn matches should only compare stored LinkedIn reference URLs. The skill must not scrape LinkedIn.

## Odoo Query Guidance

Use Odoo MCP `search_read` with array domains, not string domains. Prefer batched lookups where practical. Suggested fields:

- `mailing.contact`: `id`, `email`, `name`, `company_name`, `opt_out`, `is_blacklisted`
- `crm.lead`: `id`, `name`, `email_from`, `partner_name`, `website`, `contact_name`, `active`
- `res.partner`: `id`, `name`, `email`, `website`, `is_company`, `active`
- `mail.blacklist`: `id`, `email`, `active`

Do not assume every Odoo database has every optional custom field. If a field read fails, retry with the core fields needed for matching and record the limitation in the run summary.

## Import Guard

When uploading to Odoo, import only rows where all are true:

- `odoo_ready=yes`
- `contact_email` is present
- `odoo_duplicate != yes`
- `odoo_import_eligible != no`
- no active blacklist match
- no opt-out or blacklisted existing contact was found during the upload-time safety check

Report duplicate skips separately from no-email skips, blacklist skips, and validation errors.

## Skill Edits

Update `SKILL.md` to:

- mention Odoo duplicate screening in the frontmatter description
- add duplicate screening to First Steps
- add a dedicated "Odoo Duplicate Screen" section before "Odoo Mailing List Upload"
- update Output Review to include the new fields
- update upload rules to skip Odoo duplicates
- update Quality Bar to require duplicate marking and import skipping

Update `references/handoff-schema.md` only if it documents the workbook/export schema enough that the new fields need to be listed there.

Update `scripts/lead_crawler.py` and tests if the workbook writer has a fixed column list. The script should preserve duplicate annotations supplied by the agent or by a candidates file.

## Validation

Run a schema-level test or fixture write to confirm the new fields appear in the workbook/CSV. Run existing crawler tests. Validate the skill folder with the skill validation script if available.

Forward-testing with a real Odoo MCP connection can be done later because it may touch live Odoo data. The implementation should be written so duplicate screening is read-only until the user reaches the existing upload step.
