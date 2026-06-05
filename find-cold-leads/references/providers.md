# Providers

## People source (the important one)

### Apollo MCP — primary (Mode A)
The Apollo MCP is the primary people+identity layer. Tools used:

| Tool | Cost | Returns |
|---|---|---|
| `apollo_usage_stats_credit_usage_stats` | free | lead-credit balance (read at run start) |
| `apollo_mixed_people_api_search` | **free** | `id`, `first_name`, masked last name, `title`, `organization.name`, `has_email` flag, presence flags. Filters: `person_titles`, `person_locations`, `q_organization_keyword_tags`, `per_page`, `page`. |
| `apollo_people_match` | **1 credit / matched person** (0 on no-match) | plaintext `email`, `email_status`, unmasked `last_name`, `linkedin_url`, full `organization` firmographics. Key it on the free-search `id`. |

Rules:
- **Qualify on free search; spend a credit only after Stage Q.** Never enrich to
  qualify.
- **Page** through results — `total_entries` can be thousands; a small `per_page`
  silently caps at page 1.
- Enforce the credit budget continuously (see SKILL.md "Credit gate"). Default
  budget 25.
- `reveal_personal_emails: false` (business email only — cheaper, safer).

### Mode P — generic provider CSV (deferred / not built)
If the user has an Airscale/Apollo/RocketReach **export** (CSV), the planned shape
is a column-mapping normalizer that emits the same `LEADS_COLUMNS` rows so the
qualification core runs unchanged. Until built, treat an export as manual seeds:
read it, then qualify (Stage Q) + apply compliance (Stage C) just like Apollo rows.
A licensed export is fine as a *seed*; GDPR/ePrivacy still gate *outreach*.

## Search providers (Mode O discovery)

These find candidate company URLs (not people). The script normalizes results to
`{title, url, snippet}`.

| Provider | Env var | Notes |
|---|---|---|
| `serper` | `SERPER_API_KEY` | Recommended low-cost Google SERP default. |
| `tavily` | `TAVILY_API_KEY` | Agent-oriented search with useful snippets. |
| `fixture` | none | Offline canned results JSON — for tests/self-check, no network. |

Ask which provider to use; if unsure, recommend `serper`. Do not store API keys in
files — pass `--search-api-key` for a single run or set the env var. The blocklist
+ eTLD+1 dedup run regardless of provider.

## What changed from the old skill

The previous version treated every SERP result as a company and scraped contacts
from third-party snippets. That logic is gone. Discovery now only clears obvious
junk (blocklist + dedup) and hands clean candidates to Stage Q for judgment; people
data comes from Apollo (Mode A) or the company's own pages (Mode O).
