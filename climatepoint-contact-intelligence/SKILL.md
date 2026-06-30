---
name: climatepoint-contact-intelligence
description: >
  Run the ClimatePoint Contact Intelligence pipeline: enrich raw contact CSVs via
  web research, classify contacts by persona/need/score, and produce a
  sales-ready output file. Use this skill whenever the user mentions
  ClimatePoint, contact classification, lead scoring, persona mapping, outreach
  angles, enriching a contact list, batch contact processing, or turning a raw
  CSV into a scored sales map. Also use it when the user wants to run the
  classifier script, validate keyword rules, or iterate on the rubric.
---

# ClimatePoint Contact Intelligence System

## Purpose

Turn a raw CSV of contacts (e.g., from HubSpot) into an actionable sales map.
Three phases:

1. **Enrichment** — Add Title, LinkedIn, Company, Summary, Headline via web research.
2. **Classification** — Persona, Lead Score, Need State, Opportunity, Outreach Angle, Next Action.
3. **Account Research** — Company-level fields (industry, size, regulatory exposure, etc.) for top-scored leads.

## Output format

UTF-8 with BOM (`utf-8-sig`). Required for correct Norwegian character display in Excel on Windows.

## Phase 1: Enrichment

For each contact, research and fill:

| Field | Source |
|-------|--------|
| Title | LinkedIn headline, company team page, press release |
| LinkedIn | Direct profile URL |
| Company | Current employer (not past roles) |
| Summary | 1–2 sentence bio from LinkedIn or company page |
| Headline | LinkedIn headline or latest role description |

**Critical rules:**
- Prefer current role over past roles. Strip `former`, `ex-`, `previously` prefixes before recording title.
- If no current role found, write `Unknown`.
- If company is ambiguous, check the email domain and LinkedIn for confirmation.

## Phase 2: Classification

Use the bundled `references/climatepoint_classifier.py` script for batch classification.

### Rule-based persona engine

The engine scores keywords across `normalize_text_for_keywords(title + headline + summary)`.

**Validated keyword taxonomy:**

```python
PERSONA_KEYWORDS = {
    "Sustainability Buyer": [
        "sustainability", "esg", "climate", "environment", "csr",
        "net zero", "carbon", "bærekraft", "miljø", "grønn"
    ],
    "Product / R&D Buyer": [
        "product", "r&d", "innovation", "packaging", "design",
        "engineering", "utvikling", "produkt", "cto", "chief technology",
        "scientific advisor", "research", "development"
    ],
    "Operations / Supply Chain Buyer": [
        "procurement", "supply chain", "operations", "logistics",
        "purchasing", "sourcing", "innkjøp", "drift", "coo",
        "chief operating"
    ],
    "Founder / Executive Sponsor": [
        "founder", "ceo", "managing director", "owner",
        "general manager", "daglig leder", "grundare", "adm dir",
        "chief executive"
    ],
    "Investor / Fund Persona": [
        "investment", "portfolio", "vc", "venture", "fund",
        "analyst", "partner", "investor", "private equity",
        "pe ", "capital", "finansiering", "principal",
        "managing partner", "founding partner", "investment director",
        "investment manager", "senior associate",
    ],
    "Marketing / Commercial": [
        "cmo", "marketing", "brand", "sales", "commercial",
        "communications", "salgs", "kommunikasjon"
    ],
    "Technical / Analyst": [
        "lca", "analyst", "consultant", "scientist", "researcher",
        "data", "rådgiver", "forsker", "risk", "head of risk",
        "technical"
    ],
    "Partner / Channel": [
        "consultant", "advisor", "agency", "accelerator",
        "incubator", "certification", "rådgivning"
    ],
}
```

**LOW_FIT keywords (checked against `title` only, with word boundaries):**

```python
LOW_FIT_KEYWORDS = [
    "hr", "legal", "admin", "student", "intern", "assistant",
    "receptionist", "bookkeeper", "accountant"
]
```

**Critical implementation detail — LOW_FIT check:**
- Use `re.search(rf'\b{re.escape(kw)}\b', title.lower())` for each keyword.
- Check **only** the `title` field, not headline or summary. Past roles in bios must not trigger Low-fit.
- Example: "International Business" must NOT match "intern".

**Critical implementation detail — "associate" edge case:**
- "Associate" by itself is NOT an Investor keyword.
- "Senior associate" IS an Investor keyword.
- "Associate Professor" must classify as Product / R&D Buyer or Technical / Analyst, not Investor.

### Tie-break rule

When two personas have equal keyword counts, prefer the one with higher base score:

| Persona | Base Score |
|---------|-----------|
| Sustainability Buyer | 7 |
| Investor / Fund Persona | 6 |
| Operations / Supply Chain Buyer | 6 |
| Founder / Executive Sponsor | 5 |
| Product / R&D Buyer | 5 |
| Technical / Analyst | 4 |
| Marketing / Commercial | 3 |
| Partner / Channel | 3 |

### Fund domain boost

If email domain contains `vc`, `fund`, `capital`, `partners`, `ventures`, `equity`, `pe `, or `investment`, add +1 to Investor / Fund Persona score.

### Lead scoring rubric

Start with persona base score, then apply modifiers:

| Modifier | Condition | +/- |
|----------|-----------|-----|
| C-level | Title contains CEO, CFO, COO, CTO, CMO, CSO, Chief, Founder, Owner, President | +2 |
| VP / Director | Title contains VP, Vice President, Director, Head of, Leder, Sjef, Norgessjef | +1 |
| Industry fit positive | Company/summary mentions cleantech, manufacturing, consumer goods, industri, food, beverage, energy, mobility, construction, marine, shipping, agriculture, forestry | +1 |
| Industry fit negative | Company/summary mentions oil & gas, fossil, coal, tobacco | -1 |
| Physical product signal | manufacturing, product, hardware, device, vehicle, ship, turbine | +1 |
| Supply chain signal | supply chain, procurement, sourcing, logistics | +1 |
| Investor bonus | Persona is Investor / Fund Persona | +1 |
| Regulatory exposure | CSRD, SFDR, scope 3, SBTi, TCFD, PEF mentioned | +1 |
| Competitor domain | Domain in {3degreesinc.com, 3degrees.com, ecoact.com, carbontrust.com} | cap at 3 |
| Internal contact | Domain is climatepoint.com / .no, or "climatepoint" in text | score = 0 |

Score clamped to 1–10 (0 for internal).

### Need state classification

Map persona + keyword signals to need:

| Need | Signals | Target Personas |
|------|---------|-----------------|
| Product Carbon Footprint / LCA | product, manufacturing, materials, packaging, claims | Product/R&D, Ops/SC, Sustainability |
| PEF / EU Product Environmental Footprint | eu, comparison, claims, regulation, pef | Product/R&D, Marketing, Sustainability |
| EPD / Construction Materials | building, furniture, materials, construction, marine | Ops/SC, Technical/Analyst |
| Investor Portfolio Impact | vc, fund, accelerator, sfdr, impact, portfolio | Investor |
| Scope 3 / Supplier Footprint | procurement, supply chain, supplier, scope 3, purchasing | Ops/SC, Sustainability |
| Eco-design / Product Comparison | r&d, innovation, alternative materials, design, comparison | Product/R&D |
| Sustainability Claims Validation | carbon neutral, low impact, sustainable, claims, marketing | Marketing, Sustainability |
| Data Quality / Audit Support | lca, consultant, audit, methodology, data | Technical/Analyst |

If no signals match, use persona defaults:
- Sustainability Buyer → Scope 3 / Supplier Footprint
- Investor → Investor Portfolio Impact
- Product/R&D → Product Carbon Footprint / LCA
- Ops/SC → Scope 3 / Supplier Footprint
- Marketing → Sustainability Claims Validation
- Technical/Analyst → Data Quality / Audit Support
- Founder/Executive → Not enough information
- Partner/Channel → Not enough information

### Opportunity mapping

```python
OPPORTUNITY_MAP = {
    ("Sustainability Buyer", "Scope 3 / Supplier Footprint"): "Supplier footprint LCA + absolute target setting",
    ("Sustainability Buyer", "Product Carbon Footprint / LCA"): "Product carbon footprint + reporting",
    ("Investor / Fund Persona", "Investor Portfolio Impact"): "Portfolio-level climate screening + SFDR alignment",
    ("Product / R&D Buyer", "Eco-design / Product Comparison"): "Component LCA + eco-design benchmarking",
    ("Product / R&D Buyer", "Product Carbon Footprint / LCA"): "Product carbon footprint + reporting",
    ("Operations / Supply Chain Buyer", "Scope 3 / Supplier Footprint"): "Supply chain carbon assessment + procurement criteria",
    ("Marketing / Commercial", "Sustainability Claims Validation"): "Claims audit + PEF / EPD support",
    ("Technical / Analyst", "Data Quality / Audit Support"): "Methodology review + dataset validation",
    ("Founder / Executive Sponsor", "Not enough information"): "Strategic advisory + board-level sustainability roadmap",
}
```

### Outreach angle

Use persona template by default. LLM fallback only for high-value leads (base score ≥ 6) to save tokens/time.

Templates are in `references/climatepoint_classifier.py` under `OUTREACH_TEMPLATES`.

### Next action

| Score | Action |
|-------|--------|
| 9–10 | Direct LinkedIn message + email + calendar link |
| 7–8 (Sustainability, Ops/SC, Investor) | LinkedIn connection request + personalized message |
| 7–8 (Founder, Product/R&D) | Warm intro via mutual connection or board member |
| 5–6 | Add to nurture sequence (relevant content + case study) |
| 3–4 (Partner / Channel) | Soft partnership inquiry |
| 3–4 (Other) | Deprioritize — quarterly check |
| 1–2 | Remove from active pipeline |
| 0 | Exclude from outreach — internal contact |

## Phase 3: Account Research

Run basic heuristic account research for leads with score ≥ 7 (configurable via `--deep-research-min-score`).

Fields inferred from company + summary text:
- Industry, Company Size, Revenue / Funding Stage, Country / HQ, Product Type
- Sustainability Claims, Regulatory Exposure, Has Physical Product, Has Manufacturing / Supply Chain
- Has Investors / Portfolio, Existing ESG Content, Likely LCA Need, Estimated Urgency, Recommended Offer

**Preserve existing non-empty values** when re-running (use `set_if_empty` pattern).

## Running the classifier

```bash
python climatepoint_classifier.py \
    --input "contacts_enriched.csv" \
    --output "contacts_classified.csv" \
    --ollama-host http://localhost:11434 \
    --ollama-model llama3.2 \
    --batch-size 50 \
    --resume
```

For Ollama Cloud:
```bash
python climatepoint_classifier.py \
    --input "contacts_enriched.csv" \
    --output "contacts_classified.csv" \
    --ollama-host https://ollama.com \
    --ollama-model kimi-k2.6:cloud \
    --ollama-api-key $OLLAMA_API_KEY \
    --batch-size 50
```

Resume support: `--resume` skips rows where `Persona` is already populated.

### No-shell fallback

If you cannot execute Python via Bash (e.g., permission denied), do this inline:

1. **Enrichment first** — Perform web research for each contact to find current Title, Company, Summary, Headline. Use WebSearch, WebFetch, or LinkedIn lookup. Do NOT infer titles from email domains. A `.edu` domain does not mean "Student." If research fails, record `Unknown` — never guess.
2. Read `references/climatepoint_classifier.py` into context.
3. Use the `rule_based_persona()` logic directly: normalize title, check LOW_FIT word boundaries, score PERSONA_KEYWORDS, apply tie-break, apply fund domain boost.
4. Use `score_lead()` for scoring.
5. Use `classify_need()` + `map_opportunity()` + `build_outreach_angle()` + `determine_next_action()` for downstream fields.
6. Write results to CSV with `utf-8-sig` encoding.

Never skip classification because you can't run a script. The rubric is fully executable by reading the Python code and applying it row by row.

## LLM fallback policy

Rule-based engine handles 90%+ of contacts with clear titles. LLM fallback (`OllamaClient.classify_persona`) only triggers when:
- Rule-based engine returns empty (no keyword matches)
- Title is not generic (not `unknown`, `not found`, `generic contact`, `info`, `hello`)
- Ollama client is available

This saves tokens and avoids LLM hallucination on obvious cases.

## Validation checklist

Before declaring a classified file complete:

1. **LOW_FIT word boundaries** — Search output for "Low-fit" and verify no false positives (e.g., "International" → NOT Low-fit).
2. **Associate edge case** — Search for "Associate Professor", "Research Associate", etc. Verify they are NOT classified as Investor.
3. **Title-only LOW_FIT** — Verify past roles in bios (e.g., "Former Accountant") do NOT trigger Low-fit.
4. **Score distribution** — Check that scores 9–10 are rare and justified; scores 1–2 should be Low-fit/Other or generic contacts.
5. **Internal/competitor handling** — Verify ClimatePoint domains and competitor domains score 0 or are capped at 3.

## Bundled resources

- `references/climatepoint_classifier.py` — Full batch classification script with all rubrics, templates, and Ollama integration.
- Read this file whenever you need to run the pipeline or verify keyword rules.

## Key design principles

1. **Rule-based first, LLM second** — The keyword engine is faster, cheaper, and more reliable for clear titles. Use LLM only for ambiguity.
2. **Preserve manual work** — When re-running, never overwrite non-empty fields unless explicitly instructed.
3. **Norwegian characters matter** — Always use `utf-8-sig` for Windows Excel compatibility.
4. **Current role over past role** — `normalize_text_for_keywords` strips former/ex-/previously prefixes so classification reflects present position.
