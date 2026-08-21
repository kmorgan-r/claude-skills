# Search Themes

Use these as starting points. A theme defines what the Apollo people pipeline
needs: **sectors** (Stage Q ICP fit), **`contact_search_titles`** (→ Apollo
`person_titles`), and **`lead_signals`** / **`buyer_title_terms`** (intent and
title matching for Stage Q). Adjust geography, sector language, and exclusions
before running the pipeline.

## Generic B2B

A neutral starting point when you haven't defined a niche yet. Use this to experiment, then refine into a custom theme based on what you find. **This is also the fallback of record** — when any other theme omits an optional field, the agent uses the corresponding value here (see the note at the end of this file).

- Sectors: any B2B company
- Keywords: none (relies on sector + location)
- Subthemes: Custom discovery

```json
{
  "id": "generic-b2b",
  "label": "Generic B2B",
  "sectors": ["any B2B company"],
  "keywords": [],
  "contact_search_titles": [
    "Chief Executive Officer",
    "Chief Operating Officer",
    "Vice President",
    "Director",
    "Head of Operations",
    "General Manager",
    "Procurement Manager",
    "Supply Chain Manager"
  ],
  "buyer_title_terms": [
    "chief", "vp", "vice president", "director", "head",
    "manager", "procurement", "supply chain", "operations"
  ],
  "lead_signals": [],
  "high_priority_title_terms": ["chief", "vp", "director", "head"],
  "medium_priority_title_terms": ["manager", "procurement", "supply chain", "operations"]
}
```

## DPP Rollout Sectors

Best when looking for manufacturers and brands likely to need Digital Product Passport, PEFCR, or product-level environmental data.

- Textiles, apparel, footwear
- Furniture
- Mattresses
- Toys
- Keywords: `Digital Product Passport`, `PEFCR`, `product carbon footprint`, `sustainability report`, `manufacturer`, `supplier`

```json
{
  "id": "dpp-rollout-sectors",
  "label": "DPP Rollout Sectors",
  "sectors": [
    "textiles manufacturer", "apparel manufacturer", "footwear manufacturer",
    "furniture manufacturer", "mattress manufacturer", "toys manufacturer"
  ],
  "keywords": [
    "Digital Product Passport", "PEFCR", "product carbon footprint",
    "sustainability report", "manufacturer", "supplier"
  ],
  "contact_search_titles": [
    "Head of Sustainability",
    "Sustainability Director",
    "Sustainability Manager",
    "ESG Manager",
    "Head of Procurement",
    "Procurement Manager",
    "Head of Product",
    "Product Director",
    "Compliance Manager",
    "Supply Chain Manager",
    "Director of Operations"
  ],
  "buyer_title_terms": [
    "sustainability", "esg", "procurement", "product", "compliance",
    "supply chain", "operations", "chief", "vp", "director", "head"
  ],
  "lead_signals": [
    "Digital Product Passport", "PEFCR", "product carbon footprint",
    "ISO 14067", "sustainability report", "supplier footprint", "scope 3"
  ],
  "high_priority_title_terms": ["sustainability", "esg", "procurement", "compliance"],
  "medium_priority_title_terms": ["product", "supply chain", "operations"]
}
```

> This is the theme the DPP pilot used (164 domains → 77 people → 25 enriched → 25/25 verified emails). The `contact_search_titles` list above is the title set that pilot searched; keep it as the source of truth for `person_titles` so the run is reproducible.

## EU Taxonomy LCA Requirements

Best when looking beyond DPP sectors for companies exposed to explicit life-cycle GHG requirements.

Derived from the EU Taxonomy climate-delegated-act annexes (product LCA, energy life-cycle GHG, digital ICT avoided-emissions, R&D, and adaptation activity criteria).

- Product / manufacturing LCA: low-carbon technologies, hydrogen, chlorine, organic basic chemicals, plastics in primary form
- Energy life-cycle GHG threshold: hydropower, geothermal, renewable fuels, nuclear, selected gas routes
- Digital / ICT avoided-emissions LCA: data-driven GHG reduction solutions
- R&D life-cycle performance evaluation: close-to-market R&D, direct air capture R&D
- Adaptation Annex LCA-style requirement: adaptation activities cross-referencing mitigation life-cycle criteria
- Keywords: `life-cycle GHG`, `ISO 14067`, `ISO 14064-1`, `Commission Recommendation 2013/179/EU`, `third-party verification`

```json
{
  "id": "eu-taxonomy-lca",
  "label": "EU Taxonomy LCA Requirements",
  "sectors": [
    "low-carbon technology manufacturer", "hydrogen producer",
    "chemicals manufacturer", "plastics in primary form",
    "hydropower operator", "geothermal operator", "renewable fuels producer",
    "digital ICT solution provider", "R&D organization"
  ],
  "keywords": [
    "life-cycle GHG", "ISO 14067", "ISO 14064-1",
    "Commission Recommendation 2013/179/EU", "third-party verification", "LCA"
  ],
  "contact_search_titles": [
    "Head of Sustainability",
    "Sustainability Director",
    "ESG Manager",
    "Head of R&D",
    "R&D Director",
    "Head of Engineering",
    "Environmental Manager",
    "LCA Manager",
    "Head of Product",
    "Chief Sustainability Officer",
    "VP Sustainability"
  ],
  "buyer_title_terms": [
    "sustainability", "esg", "r&d", "research", "engineering",
    "environmental", "lca", "product", "chief", "vp", "director", "head"
  ],
  "lead_signals": [
    "life-cycle GHG", "ISO 14067", "ISO 14064-1",
    "Commission Recommendation 2013/179/EU", "third-party verification",
    "life cycle assessment", "carbon footprint", "taxonomy"
  ],
  "high_priority_title_terms": ["sustainability", "esg", "environmental", "lca"],
  "medium_priority_title_terms": ["r&d", "research", "engineering", "product"]
}
```

## Standards-Triggered Prospects

Best when searching for companies already using language that maps directly to your offer.

- Keywords: `ISO 14067`, `ISO 14064-1`, `PEFCR`, `product environmental footprint`, `product carbon footprint`, `third-party verified`
- Useful source types: sustainability pages, PCF pages, annual reports, EPD pages, supplier pages, press releases

```json
{
  "id": "standards-triggered-prospects",
  "label": "Standards-Triggered Prospects",
  "sectors": ["any B2B manufacturer exposed to product environmental standards"],
  "keywords": [
    "ISO 14067", "ISO 14064-1", "PEFCR",
    "product environmental footprint", "product carbon footprint",
    "third-party verified"
  ],
  "contact_search_titles": [
    "Head of Sustainability",
    "Sustainability Director",
    "Sustainability Manager",
    "Quality Manager",
    "Head of Quality",
    "Compliance Manager",
    "Environmental Manager",
    "Head of Product",
    "Procurement Manager",
    "Chief Sustainability Officer",
    "VP ESG"
  ],
  "buyer_title_terms": [
    "sustainability", "esg", "quality", "compliance", "environmental",
    "product", "procurement", "chief", "vp", "director", "head"
  ],
  "lead_signals": [
    "ISO 14067", "ISO 14064-1", "PEFCR",
    "product environmental footprint", "product carbon footprint",
    "third-party verified", "environmental product declaration", "EPD"
  ],
  "high_priority_title_terms": ["sustainability", "esg", "compliance", "environmental"],
  "medium_priority_title_terms": ["quality", "product", "procurement"]
}
```

## LinkedIn-Assisted Cross-Reference

Use only when the user supplies a list of LinkedIn profile URLs (manual or licensed
LinkedIn data). It runs the **same Apollo people pipeline** as the other themes —
only the Phase 1 search key differs.

- **Ingest = LinkedIn profile URLs**, not company domains. The user hands you a list
  of `https://www.linkedin.com/in/…` URLs.
- **Phase 1 — free people search (0 credits):** call `apollo_mixed_people_api_search`
  with `person_linkedin_urls` (the user's URL list, batched per call; page through
  while `has_more`) **instead of** `q_organization_domains_list` + `person_titles`.
  Because the URLs already name the people, `contact_search_titles` is **not** a
  search filter in this theme — it is kept in the JSON below for schema consistency
  and to document which titles Stage Q treats as buyers. The free result returns the
  same fields as the domain pipeline (`id`, masked name, `title`, `has_email`,
  `organization.name`).
- **Stage Q qualify** on the free signals (org industry + title + resolved domain)
  using `sectors` / `buyer_title_terms` / `lead_signals` — identical to the domain
  pipeline. `strong` / `possible` proceed; `reject` dropped. **Never enrich to
  qualify** — Stage Q runs on the free result, before any paid match.
- **Phase 2 → export → Odoo upload:** rank + cap 3/company, paid
  `apollo_people_bulk_match` (1 credit/match, budget default 25), dedup + suppression
  vs Odoo, Excel export, gated Odoo upload — all identical to the domain pipeline.
- Do not crawl LinkedIn. Do not automate login, session browsing, connection graph
  collection, profile scraping, or contact extraction from LinkedIn. Store LinkedIn
  URLs only as references (`linkedin_reference_url`).

```json
{
  "id": "linkedin-assisted-cross-reference",
  "label": "LinkedIn-Assisted Cross-Reference",
  "sectors": ["any B2B company"],
  "keywords": [],
  "contact_search_titles": [
    "Chief Executive Officer",
    "Chief Operating Officer",
    "Vice President",
    "Director",
    "Head of Operations",
    "General Manager",
    "Procurement Manager",
    "Supply Chain Manager"
  ],
  "buyer_title_terms": [
    "chief", "vp", "vice president", "director", "head",
    "manager", "procurement", "supply chain", "operations"
  ],
  "lead_signals": [],
  "high_priority_title_terms": ["chief", "vp", "director", "head"],
  "medium_priority_title_terms": ["manager", "procurement", "supply chain", "operations"]
}
```

> `contact_search_titles` is unused as a search filter in this theme (Phase 1 keys on
> `person_linkedin_urls`, not titles). It is retained so Stage Q title matching and
> the field-reference table stay consistent across themes. If a user-supplied URL
> resolves to a person whose title is off-ICP, Stage Q tiers it `reject` on the free
> result — no credit spent.

## Custom Theme Inputs

For a custom theme, ask for:

- Sector or activity type (→ `sectors`, Stage Q ICP fit)
- Geography (→ Apollo `person_locations` / `organization_locations`)
- Buying trigger or compliance driver (→ `lead_signals`, Stage Q intent)
- Buyer job titles (→ `contact_search_titles`, Apollo `person_titles`)
- Required keywords
- Excluded terms or domains
- Maximum contacts and credit budget
- Output filename

Then translate into a custom theme JSON with the extended schema:

```json
{
  "id": "my-theme",
  "label": "My Theme",
  "sectors": ["..."],
  "keywords": ["..."],
  "subthemes": ["..."],
  "target_personas": "...",
  "contact_search_titles": ["..."],
  "buyer_title_terms": ["..."],
  "lead_signals": ["..."],
  "high_priority_title_terms": ["..."],
  "medium_priority_title_terms": ["..."]
}
```

### Field reference

| Field | Purpose | Required |
|-------|---------|----------|
| `id` | Theme identifier | Yes |
| `label` | Human-readable name | Yes |
| `sectors` | ICP sectors for Stage Q fit | Yes |
| `keywords` | Intent keywords (Stage Q evidence) | No |
| `subthemes` | Worksheet labels / tags | No |
| `target_personas` | Description of ideal buyer roles | No (defaults to generic) |
| `contact_search_titles` | Job titles → Apollo `person_titles` | No (defaults to generic exec titles) |
| `buyer_title_terms` | Terms that indicate a relevant contact title (Stage Q) | No (defaults to generic exec terms) |
| `lead_signals` | Intent keywords for Stage Q | No (defaults to none) |
| `high_priority_title_terms` | Terms that boost title-match confidence | No |
| `medium_priority_title_terms` | Terms that boost title-match confidence | No |

Missing optional fields fall back to the **`generic-b2b` values above**. This is an **agent convention you apply at run time**, not an automatic code path: the pipeline has no script behind it, so there is no silent fallback — you must substitute the `generic-b2b` value yourself when a prebuilt or custom theme omits a field. If a theme omits `contact_search_titles`, use the `generic-b2b` list; **never invent buyer titles ad hoc**, or the run is not reproducible.
