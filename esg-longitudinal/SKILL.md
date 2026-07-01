---
name: esg-longitudinal
description: >
  Build and re-run an agent that tracks companies' ESG / CSR / sustainability
  commitments over time using free public data. Given a company (or a list), a
  domain (climate, circular economy, biodiversity, social/governance), and a time
  range, it finds free sustainability and annual reports, extracts the targets and
  metrics into a tidy time-series, saves a timestamped snapshot, and diffs against
  earlier snapshots to surface what changed. Use this skill whenever the user wants
  to analyze ESG/CSR/sustainability data over time, track a company's climate or
  circular-economy or biodiversity or social targets, compare disclosures across
  years, build a longitudinal ESG dataset, extract metrics from sustainability
  report PDFs, monitor changes in corporate sustainability commitments, or set up
  an analysis they can re-run next year to see what moved. Also use it when the
  user mentions ESG databases, CSR reports, sustainability reports, Scope 1/2/3 or
  net-zero or circular-revenue targets, or scaling ESG analysis across many
  companies (hundreds to tens of thousands).
---

# ESG Longitudinal Tracker

## What this does and why

ESG analysis is usually a one-off snapshot: pull a company's current scores, write
a report, done. The value the user actually wants is **change over time** — both
*backward* (how did Philips' circular revenue move from 2015 to today?) and
*forward* (re-run a year from now and show what shifted).

To make "re-run in a year" actually work, every run must leave behind a **durable,
timestamped snapshot** in a stable schema. The future run doesn't re-derive
history — it just adds a new snapshot and diffs. That snapshot-first discipline is
the whole point. A beautiful report with no saved snapshot is a dead end.

The workflow: **scope → resolve → find → fetch → extract → map → snapshot → diff →
report.** Scripts handle the deterministic plumbing (search, download, parse, CSV,
diff); you (the agent) do the part that needs judgment — reading report text and
mapping it to indicators with sources.

## The tidy schema (one row per company-indicator-period)

Everything normalizes to this long format. Any domain or new indicator is just more
rows — no schema change — which is what keeps the skill flexible and makes diffing a
simple key match.

```
entity, lei, domain, indicator, value, unit, period, status,
source, source_url, page, quote, retrieved_at
```

- `period` = the **reporting year** the value describes (2022). Distinct from
  `retrieved_at` = the **run date** you pulled it (2026-06-29). Two time axes; keep
  them separate or the longitudinal logic breaks.
- `status` = `found` | `not_found` | `target` (a forward-looking goal, e.g. "25% by
  2025").
- `quote` = a short verbatim snippet from the source that contains the number.
  Required for `found`/`target` rows — see Provenance below.

Example row (Philips):
`Royal Philips | 724500... | circular | circular_revenue_pct | 18 | % | 2022 | found | Annual Report 2022 | https://… | p.41 | "circular revenues accounted for 18% of sales" | 2026-06-29`

## Provenance and anti-hallucination (read this twice)

ESG numbers are extremely easy to confabulate — plausible percentages that were
never disclosed. A longitudinal dataset is only worth building if every value is
traceable, because next year's run will be compared against it.

Rules:
- A `found` or `target` value **must** carry `source_url`, `period`, and a verbatim
  `quote`. No quote → you don't have the number → record `status: not_found`.
- Never interpolate or "estimate" a missing year to make a series look complete.
  Gaps are data. `not_found` is a valid, useful row.
- Prefer the company's **own audited report** over third-party summaries or news.
  Use news only to locate the report, not as the value's source.
- If two sources disagree (e.g. a figure restated in a later report), record both
  with their periods/sources; the diff step is exactly what surfaces restatements.

`scripts/snapshot.py` enforces the `found ⇒ value+source_url+quote` rule and will
reject rows that violate it. Do not weaken the rule to get past it — fix the data.

## Workflow

### 1. Scope
From the request, pin down: **entity/entities**, **domain(s)**, **time range**, and
the **indicators** of interest. Pull canonical indicator names + units from
`references/indicators.yaml` so values align across companies and years. If the user
named a domain loosely ("their recycling goals"), map it to the indicator pack
(circular → `waste_recycled_pct`, `circular_revenue_pct`, `product_takeback_scope`).

### 2. Resolve (entity ID)
For a single well-known company you can skip this. For matching across time or across
a list, anchor each entity to its **LEI** via the free GLEIF API (no key) so a
rename/merger doesn't break the join next year. See `references/data_sources.md`.

### 3. Find reports
Locate free report PDFs covering the years in scope:

```bash
python scripts/find_reports.py --company "Royal Philips" --years 2015-2024 --domain-hint "circular economy"
```

It queries DuckDuckGo (no key) for `"<company>" sustainability report <year>
filetype:pdf` etc. If the `ddgs` package isn't installed it prints the exact queries
to run — in that case use your own WebSearch tool with those queries. The most
reliable single source is usually the company's own **report archive / investor
relations** page; check it directly too.

### 4. Fetch
Download each chosen PDF (no key):

```bash
python scripts/fetch_pdf.py --url <pdf-url> --out data/raw/philips_2022.pdf
```

It validates the file is really a PDF (a common failure is grabbing an HTML landing
page). If `is_pdf: false`, open the URL, find the direct `.pdf` link, retry.

### 5. Extract
Pull text (and tables) locally with PyMuPDF / pdfplumber — no API, no limits:

```bash
# find just the pages that mention your indicators, to keep context small
python scripts/extract_pdf.py --pdf data/raw/philips_2022.pdf --grep "circular,take-back,recycl,scope 3"
# dump tables on the pages that matter
python scripts/extract_pdf.py --pdf data/raw/philips_2022.pdf --tables --pages 38-60
```

`--grep` prints matching pages with numbers so you can jump straight to the figures
instead of reading 200 pages. Use the printed page numbers as your `page` provenance.

### 6. Map to indicators (your judgment)
Read the extracted text / tables and write tidy rows. For each indicator × period you
can support with a quote, emit a row; for ones you looked for and couldn't find, emit
a `not_found` row so the gap is explicit. Save rows as JSON for the snapshot step.

### 7. Snapshot (always — even on the first run)
```bash
python scripts/snapshot.py --rows rows.json --run-date 2026-06-29
# -> data/snapshots/2026-06-29.csv
```

This is the durable baseline. Do it every run. Without it there is nothing for a
future run to diff against, and the longitudinal promise is broken.

### 8. Diff (when a prior snapshot exists)
```bash
python scripts/diff.py --old data/snapshots/2026-06-29.csv \
                       --new data/snapshots/2027-06-29.csv --out reports/change_2027.md
```

Matches on `(entity, indicator, period)` → reports **new**, **changed**, **dropped**
values with numeric deltas. On the first run there's no prior snapshot; that's
expected — the backward trend still comes from the multiple `period` rows you just
captured.

### 9. Report
Produce a markdown report with: a tidy time-series table (the backward trend), the
change report from step 8 if applicable, and a short narrative. Template below.

## Output: report structure

```markdown
# {Company} — {Domain} over time ({year range})

## Time series
| indicator | unit | {2015} | {2018} | {2020} | {2022} | target |
|---|---|---|---|---|---|---|
(one row per indicator; cells blank where not_found)

## What changed since last snapshot
(diff.py output, or "first snapshot — baseline established")

## Notable trajectory
(2–4 sentences: direction of travel, gaps, restatements, target vs actual)

## Sources
(every report used, with URL and year)
```

## First run vs re-run (the longitudinal payoff)

- **First run:** establishes the baseline snapshot + the backward trend from
  historical reports. There is no diff yet; say so plainly.
- **Re-run (months/a year later):** you may be handed a prior snapshot CSV, or find
  it under `data/snapshots/`. Pull the newest reports, write a fresh snapshot dated
  today, then diff against the prior one. The change report *is* the deliverable —
  new disclosures, restated figures, targets met or missed.

## Scaling from one company to many (hundreds → 36k)

The single-company flow above is the unit of work. To scale:
- Drive a **universe list** (CSV of entities + LEIs). Resolve LEIs once via GLEIF.
- **Tier the extraction to control cost:** pull cheap structured sources first
  (see `references/data_sources.md` — WikiRate, SBTi, SEC EDGAR) for the whole list;
  reserve expensive PDF download + parse for the subset where structured data is
  missing or thin (often biodiversity / circular).
- **Cache** raw PDFs and extracted text under `data/raw/` and skip re-downloading.
- Snapshot the whole universe into one dated CSV; `diff.py` handles many entities at
  once (the key includes `entity`).
- A clean free universe won't reach 36k — realistically ~5–15k with usable free data.
  To go broader later, add a paid adapter (e.g. FMP Premium ESG, ESG Book, CSRHub)
  as just another `source`; the schema and diff are unchanged.

## Free data sources

`references/data_sources.md` is the catalog: what each source covers, whether it has
an API or needs scraping, and which domains it's strong/weak on. Read it when
choosing where to pull from. Quick orientation:

- **GLEIF** — free entity IDs (LEI). The spine that ties records across time.
- **WikiRate API** — free, open, multi-domain structured metrics. Primary structured
  source.
- **SBTi** — free downloadable list of validated climate targets. Climate depth.
- **SEC EDGAR** — free full-text API, US filers.
- **Report PDFs** (company IR archives, sustainabilityreports.com) — the fallback for
  anything the structured sources miss; this skill's find→fetch→extract chain.

Free coverage is strongest for **climate**, decent for **social/governance**, thin
for **circular economy** and thinnest for **biodiversity** — expect more PDF
extraction the further you get from climate.

## Bundled resources

- `scripts/find_reports.py` — find candidate report PDFs (DuckDuckGo, no key).
- `scripts/fetch_pdf.py` — download + validate a PDF (no key).
- `scripts/extract_pdf.py` — PyMuPDF/pdfplumber text + table extraction, `--grep`.
- `scripts/snapshot.py` — write/validate a timestamped snapshot CSV (enforces
  provenance).
- `scripts/diff.py` — diff two snapshots → markdown change report.
- `references/indicators.yaml` — canonical indicator packs per domain.
- `references/data_sources.md` — free ESG/CSR data source catalog + API notes.

## Key design principles

1. **Snapshot-first.** Every run writes a dated snapshot, or the longitudinal promise
   is broken. The report is downstream of the snapshot, not a replacement for it.
2. **Provenance or it didn't happen.** Every value needs a source and a verbatim
   quote. `not_found` beats a confident guess.
3. **One tidy schema, many domains.** New indicators are new rows, never a rewrite.
   This is what lets the same skill cover climate, circular, biodiversity, social.
4. **Two time axes.** `period` (reporting year) ≠ `retrieved_at` (run date). Keep
   them distinct.
5. **Scripts for plumbing, you for judgment.** Deterministic steps (search, download,
   parse, diff) are scripted and reusable; extraction/mapping is where your reading
   adds value.
