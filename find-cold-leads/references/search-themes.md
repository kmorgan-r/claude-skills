# Search Themes & Intent Signals

The lesson from the failed run: **generic keyword search ("textile manufacturer
Germany") returns listicles and directories.** Quality candidates come from
**intent** — companies already publishing the signals that map to ClimatePoint's
offer — and from **curated seed lists**.

## Intent signals (lead with these)

Query for the signal first, then narrow by sector + region:

- `"ISO 14067"`, `"ISO 14064-1"`
- `"PEFCR"`, `"product environmental footprint"`
- `"product carbon footprint"`, `"PCF"`
- `"EPD"`, `"environmental product declaration"`
- `"Digital Product Passport"`
- `"life cycle assessment"` / `"LCA"`
- `"sustainability report" filetype:pdf`

The script's `expand_queries()` already crosses these signals with the theme's
sectors and the location. A company publishing one of these is in-market by
definition.

## Themes

### `dpp-rollout-sectors` (recommended)
ClimatePoint's current ICP. Sectors: textiles / apparel / footwear, furniture,
mattresses, toys. EU DPP deadlines 2027–2030.

### `eu-taxonomy-lca`
Broader life-cycle-GHG-exposed manufacturers beyond DPP sectors (low-carbon
technologies, chemicals/plastics in primary form, energy life-cycle thresholds,
ICT avoided-emissions). Source workbook:
`C:\Users\kmorg\Downloads\eu_taxonomy_lca_requirements.xlsx`.

### `standards-triggered-prospects`
Companies already using ClimatePoint's exact language (ISO 14067 / PEFCR / PCF /
EPD / third-party verified). Strongest signal — these are self-identified.

## Curated seed sources (better than open search)

When you can, seed discovery from authoritative member/exhibitor lists rather than
SERPs — they are pre-filtered to real operating companies:

- Trade-association member directories (textile, furniture, toy federations).
- Trade-show exhibitor lists (e.g. Heimtextil, imm cologne, Spielwarenmesse).
- EPD-programme registries (e.g. EPD International, IBU) — companies with published
  EPDs are maximally in-market.

Extract company domains from these pages, then qualify each with Stage Q. Members
of an EPD registry are far higher-intent than a SERP result.

## LinkedIn-assisted cross-reference

Only when the user supplies manual/licensed LinkedIn data. Do not crawl LinkedIn.
Store URLs as references; find contact evidence on the company's own public pages.

## Custom theme inputs

Ask for: sector/activity, geography, buying trigger / compliance driver, required
keywords, excluded terms/domains, max leads, credit budget, output filename.
Custom theme JSON: `{ "id", "label", "sectors": [...] }` via `--custom-theme-file`.
