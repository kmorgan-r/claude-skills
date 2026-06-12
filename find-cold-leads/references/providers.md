# Providers

This skill has two halves: an **Apollo MCP** people/identity layer (agent-driven,
credit-metered) and an open-web **discovery + extraction** layer implemented by
`scripts/lead_crawler.py`. Qualification (Stage Q, see SKILL.md) runs on free
signals in both, and a credit is spent only after a row qualifies.

## People source — Apollo MCP (Mode A)

The Apollo MCP is the primary people+identity layer, used by the agent (not the
script). Tools:

| Tool | Cost | Returns |
|---|---|---|
| `apollo_usage_stats_credit_usage_stats` | free | lead-credit balance (read at run start) |
| `apollo_mixed_people_api_search` | **free** | `id`, `first_name`, masked last name, `title`, `organization.name`, `has_email` flag. Filters: `person_titles`, `person_locations`, `q_organization_keyword_tags`, `per_page`, `page`. |
| `apollo_people_match` | **1 credit / matched person** (0 on no-match) | plaintext `email`, `email_status`, unmasked `last_name`, `linkedin_url`, full `organization` firmographics. Key it on the free-search `id`. |

Rules:
- **Qualify on free search; spend a credit only after Stage Q.** Never enrich to qualify.
- **Page** through results — `total_entries` can be thousands; a small `per_page` silently caps at page 1.
- Enforce the credit budget continuously (see SKILL.md "Credit Gate"). Default budget 25.
- `reveal_personal_emails: false` (business email only — cheaper, safer).

A licensed Apollo/RocketReach/Airscale **export** (CSV) is fine as a *seed*: pass
it via `--manual-seeds`, then qualify (Stage Q) and apply the compliance wall
just like any other row. GDPR/ePrivacy still gate *outreach*, not holding a seed.

## Search providers (Mode O discovery)

Open-web account discovery via `scripts/lead_crawler.py --search-provider`.
These find candidate company URLs (not people). Results are normalized to
`{title, link, snippet}`; the blocklist + eTLD+1 dedup run regardless of
provider. Run `--list-providers` for the live list.

| Provider | Env var(s) | Notes |
|---|---|---|
| `serper` | `SERPER_API_KEY` | Recommended low-cost Google SERP default. |
| `serpapi` | `SERPAPI_KEY` | SerpApi Google Search. |
| `searchapi` | `SEARCHAPI_API_KEY` | SearchApi.io Google Search. |
| `brave` | `BRAVE_SEARCH_API_KEY` | Brave Search API. |
| `tavily` | `TAVILY_API_KEY` | Agent-oriented search with useful snippets. |
| `exa` | `EXA_API_KEY` | Neural/keyword search. |
| `google_cse` | `GOOGLE_API_KEY` **+** `GOOGLE_CSE_ID` | Needs both the API key and the search-engine id. |
| `codex_manual` | none | No automated search; discovery comes from `--manual-seeds` (or `--fixture`). |

Offline testing uses the separate `--fixture <file.json>` flag, not a provider.

## Extract providers (page enrichment)

`--extract-provider` (default `codex_builtin`): fetch a company page's text,
links, and emails.

| Provider | Env var | Notes |
|---|---|---|
| `codex_builtin` | none | Built-in `requests` + BeautifulSoup. **Do not use on cloud VMs / sensitive internal networks** — it fetches from this machine (DNS-rebinding SSRF risk). |
| `jina` | `JINA_API_KEY` (optional) | Jina Reader; markdown. |
| `firecrawl` | `FIRECRAWL_API_KEY` | Firecrawl Scrape; markdown + HTML. |
| `tavily` | `TAVILY_API_KEY` | Tavily Extract; text. |
| `exa` | `EXA_API_KEY` | Exa Contents; text. |

The API extractors fetch from the provider's infrastructure, so prefer them in
cloud environments.

## Key handling

Do **not** store API keys in files. Pass `--search-api-key` / `--extract-api-key`
for a single run, set the env var, or use `--prompt-for-keys`. For `google_cse`,
also set `GOOGLE_CSE_ID`. Keys are scrubbed from HTTP-error text, logs, and
workbook cells.

## Contact provenance

Contact evidence (page or `--contact-search`) must come from the company's own
registrable domain (`contact_provenance_ok` in the script), never a third-party
data-vendor snippet. See `source-compliance.md`. The script's `LEAD_COLUMNS`
constant is the canonical export schema (see `handoff-schema.md`).
