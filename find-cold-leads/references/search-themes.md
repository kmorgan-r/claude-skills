# Search Themes

Use these as starting points. Adjust geography, sector language, and exclusions before running the crawler.

## Generic B2B

A neutral starting point when you haven't defined a niche yet. Use this to experiment, then refine into a custom theme based on what you find.

- Sectors: any B2B company
- Keywords: none (relies on sector + location)
- Subthemes: Custom discovery

## DPP Rollout Sectors

Best when looking for manufacturers and brands likely to need Digital Product Passport, PEFCR, or product-level environmental data.

- Textiles, apparel, footwear
- Furniture
- Mattresses
- Toys
- Keywords: `Digital Product Passport`, `PEFCR`, `product carbon footprint`, `sustainability report`, `manufacturer`, `supplier`

## EU Taxonomy LCA Requirements

Best when looking beyond DPP sectors for companies exposed to explicit life-cycle GHG requirements.

Derived from the EU Taxonomy climate-delegated-act annexes (product LCA, energy life-cycle GHG, digital ICT avoided-emissions, R&D, and adaptation activity criteria).

- Product / manufacturing LCA: low-carbon technologies, hydrogen, chlorine, organic basic chemicals, plastics in primary form
- Energy life-cycle GHG threshold: hydropower, geothermal, renewable fuels, nuclear, selected gas routes
- Digital / ICT avoided-emissions LCA: data-driven GHG reduction solutions
- R&D life-cycle performance evaluation: close-to-market R&D, direct air capture R&D
- Adaptation Annex LCA-style requirement: adaptation activities cross-referencing mitigation life-cycle criteria
- Keywords: `life-cycle GHG`, `ISO 14067`, `ISO 14064-1`, `Commission Recommendation 2013/179/EU`, `third-party verification`

## Standards-Triggered Prospects

Best when searching for companies already using language that maps directly to your offer.

- Keywords: `ISO 14067`, `ISO 14064-1`, `PEFCR`, `product environmental footprint`, `product carbon footprint`, `third-party verified`
- Useful source types: sustainability pages, PCF pages, annual reports, EPD pages, supplier pages, press releases

## LinkedIn-Assisted Cross-Reference

Use only when the user supplies manual or licensed LinkedIn data.

- Do not crawl LinkedIn.
- Do not automate login, session browsing, connection graph collection, profile scraping, or contact extraction from LinkedIn.
- Store LinkedIn URLs only as references.
- Find actual contact evidence from public non-LinkedIn pages such as company websites, press pages, team pages, conference speaker pages, filings, and official contact pages.

## Custom Theme Inputs

For a custom theme, ask for:

- Sector or activity type
- Geography
- Buying trigger or compliance driver
- Required keywords
- Excluded terms or domains
- Maximum leads
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
| `sectors` | Search query sectors | Yes |
| `keywords` | Search query keywords | No |
| `subthemes` | Worksheet labels / tags | No |
| `target_personas` | Description of ideal buyer roles | No (defaults to generic) |
| `contact_search_titles` | Job titles to search for per company | No (defaults to generic exec titles) |
| `buyer_title_terms` | Keywords that indicate a relevant contact title | No (defaults to generic exec terms) |
| `lead_signals` | Snippet keywords that boost the lead score | No (defaults to none) |
| `high_priority_title_terms` | Terms that boost contact confidence (+25) | No |
| `medium_priority_title_terms` | Terms that boost contact confidence (+15) | No |

Missing optional fields fall back to generic B2B defaults, so old themes without the new fields still work.
