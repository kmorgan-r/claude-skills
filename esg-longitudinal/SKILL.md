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
source, source_url, page, quote, retrieved_at,
item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status,
smart_specific, smart_achievable, smart_relevant, substance,
planetary_alignment, impact_scope, priority_internal, importance_external,
linked_targets, assessment_notes
```

The first 13 columns are the original core; the next 6 are the v1 **classification
layer** and the last 10 are the v2 **SMART+ target-quality** block (see sections
below). All 16 non-core columns are optional — old 13-column and 19-column snapshots
still validate, and `diff.py` keys on `(entity, indicator, period)` regardless.

- `period` = the **reporting year** the value describes (2022). Distinct from
  `retrieved_at` = the **run date** you pulled it (2026-06-29). Two time axes; keep
  them separate or the longitudinal logic breaks. **For `status=target` rows, set
  `period` = the target's end year** (e.g. 2025) so a target never collides with the
  same indicator's actual (period = disclosure year) under the snapshot key.
- `status` = `found` | `not_found` | `target` (a forward-looking goal, e.g. "25% by
  2025").
- `quote` = a short verbatim snippet from the source that contains the number.
  Required for `found`/`target` rows — see Provenance below.
- `item_type` = `kpi` (a measured metric/indicator) | `target` (a forward-looking
  goal) | `qualitative` (a narrative commitment with no number). Auto-filled from
  `status` when blank (found→kpi, target→target).
- `r_strategy` = which of the 10 R-strategies (R0–R9) a circular commitment
  advances; pipe-separated with the primary first (e.g. `R2|R8`). See
  `circular-economy-10rs.json`. Blank for non-circular rows.
- `enabler_topic` = for commitments that *enable* circularity rather than being an
  R-strategy themselves (training, data infrastructure, R&D, …); one of the enabler
  ids in `circular-economy-10rs.json`. Independent of `r_strategy`.
- `target_end_year` / `target_has_kpi` = target completeness (see Target anatomy).
- `target_status` = year-over-year outcome (see Year-over-year target status).

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

## Classification layer (circular economy)

Beyond the raw value, classify each circular row so the dataset is queryable and
comparable across companies:

- **R-strategy** (`r_strategy`): map the commitment to R0–R9 using
  `circular-economy-10rs.json`. Shorter loops (R0 Refuse … R2 Reduce) are more
  circular than long loops (R8 Recycle, R9 Recover). Use `references/indicators.yaml`
  `r_hint` as a starting point, but read the text — a "recycled content" pledge is
  R8, a "designed-out packaging" pledge is R0/R2. Pipe-separate when a commitment
  genuinely spans strategies (primary first).
- **Enablers** (`enabler_topic`): some commitments are not an R-strategy but make
  them possible — one of the 11 enabler ids in `circular-economy-10rs.json`. Prefer
  `traceability` for product/material passports and chain-of-custody, `measurement`
  for metering / KPI / impact accounting, and `data_infrastructure` only for broad
  digital-systems commitments; the others are `ecodesign`, `rnd`, `procurement`,
  `training`, `partnerships`, `reverse_logistics`, `finance`, `policy`. A row is
  usually an R-strategy item *or* an enabler item; occasionally both.

## Target anatomy

For `status=target` rows, record completeness so you can tell a hard commitment from
an aspiration:

- `target_end_year` — the deadline year (`2025`), or blank if none stated. This is
  also the row's `period` for targets (see the schema note above).
- `target_has_kpi` — `yes` if the target carries a quantified value/KPI ("25%
  circular revenue"), `no` if it is directional only ("become fully circular").
- Derived **completeness** (report only): `both` (kpi + year) → fully specified;
  `kpi_only` → no deadline; `year_only` → deadline but no metric; `none` → vague
  aspiration.

## Year-over-year target status

Once a target appears in more than one year, record what happened to it in
`target_status`:

- `on_track` — actuals moving toward the target, deadline unchanged.
- `achieved` — the target was met (an actual now meets/exceeds it).
- `delayed` — deadline pushed out.
- `changed` — target value or scope restated.
- `failed` — deadline passed without meeting the target.
- `dropped` — the target disappeared from disclosure.
- `too_early` — first year seen; not yet assessable.

Record this from what the report states. `scripts/diff.py` independently produces a
**Target movements** section (new / changed / dropped targets, plus a target-vs-actual
table) by comparing `status=target` rows across snapshots — use it to catch silent
changes the report does not admit, and reconcile against your `target_status`.

## Target quality (SMART+)

For `status=target` rows you may add a per-target quality/materiality assessment.
These 10 columns are all optional and populate on target rows; each is validated
only when present.

- **S / A / R** (`smart_specific`, `smart_achievable`, `smart_relevant`) — `yes|no`.
  The **M** and **T** of SMART are *not* separate columns: `target_has_kpi` is M and
  `target_end_year` is T (both factual — see the Target scorecard for them).
- `substance` — `symbolic|substantive`: a real operational commitment vs signaling.
- `planetary_alignment` — `insufficient|pb_aligned|unknown`: aligned to a planetary
  boundary / science-based pathway. `unknown` is a real finding (we checked, can't
  tell) and is distinct from leaving the field blank ("not assessed").
- `impact_scope` — `A|B|C|D` (Lukas's A–D scoping, **not** GHG Scope 1/2/3):
  **A** = footprint, own operations; **B** = footprint, direct value chain (suppliers
  + use phase); **C** = footprint, broader/enabled system; **D** = **handprint**
  (positive contribution / avoided impact elsewhere). Blank = not assessed.
- `priority_internal` / `importance_external` — `high|low`: strategic priority inside
  the company vs external signaling importance.
- `linked_targets` — free text: which other (ESG) targets this connects to, and how.
- `assessment_notes` — free text rationale. **Required** whenever any of the eight
  judgment columns (`smart_specific`, `smart_achievable`, `smart_relevant`,
  `substance`, `planetary_alignment`, `impact_scope`, `priority_internal`,
  `importance_external`) is set — opinion is permitted but never ungrounded. A
  judgment value with empty `assessment_notes` is rejected by `snapshot.py`.

## Workflow

### 1. Scope
From the request, pin down: **entity/entities**, **domain(s)**, **time range**, and
the **indicators** of interest. Pull canonical indicator names + units from
`references/indicators.yaml` so values align across companies and years. If the user
named a domain loosely ("their recycling goals"), map it to the indicator pack
(circular → `waste_recycled_pct`, `circular_revenue_pct`, `product_takeback_scope`).

**Confirm the categories before running.** Unless the user already named the
domain(s) explicitly, ask which category(ies) to analyze this run — the four packs in
`references/indicators.yaml` are:

- **climate** — emissions (Scope 1/2/3), energy, net-zero / SBTi targets
- **circular** — circular revenue, recycled input, waste, take-back, material circularity
- **biodiversity** — TNFD, deforestation, land/water use, nature-positive, sourcing
- **social_gov** — diversity, safety (LTIFR), turnover, pay gap, board independence, CSRD

Ask this even when a company is given but no domain — the scope drives which reports
you fetch and which indicators you extract, so narrowing it up front keeps the run
cheap. Multi-select is fine (e.g. climate + circular); "all four" is a valid answer.
Record the chosen domain(s) — every snapshot row carries `domain`, so a partial-scope
run stays valid and a later run can add the domains you skipped.

**Confirm the time range before running.** Unless the user already gave an explicit
range ("2015–2024", "the last five years"), ask how far back to go before fetching
anything — each extra year is another report PDF to fetch and extract, so the range
drives the run's cost as much as the domain does. Propose a sensible default (the
company's available report archive, typically ~10 years) but do not assume it
silently. Record the chosen range as `--years START-END` for `find_reports.py`; a
later re-run can extend it, and the diff step compares against whatever periods this
run captured.

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

### 10. Export workbook (shareable)
Turn the snapshot CSV written in step 7 into a formatted, colored Excel workbook
with a **Legend** tab that explains every column and coded value:

```bash
pip install openpyxl   # one-time
python scripts/export_xlsx.py --snapshot data/snapshots/2026-06-29.csv \
                              --out reports/2026-06-29.xlsx
```

It reads the canonical snapshot (never modifies it) and writes an `.xlsx` with a
**Data** sheet (frozen, filtered, color-coded `status` and SMART cells) and a
**Legend** sheet (a plain-English data dictionary + code tables + color key). The
`.xlsx` is the shareable deliverable for a non-author; the CSV snapshot stays the
canonical, diff-able source of truth. `--out` defaults to
`reports/<snapshot-basename>.xlsx`. If `openpyxl` is missing the script prints an
install hint and exits non-zero.

## Output: report structure

```markdown
# {Company} — {Domain} over time ({year range})

## Time series
| indicator | R | unit | {2015} | {2018} | {2020} | {2022} | target |
|---|---|---|---|---|---|---|---|
(one row per indicator; R = r_strategy; cells blank where not_found)

## Target scorecard
| indicator | target | end year | has KPI | completeness | status |
|---|---|---|---|---|---|
(one row per status=target; completeness derived from end year + has KPI)

## Target quality (SMART+)
| indicator | S | A | R | substance | planetary | impact_scope | int.pri | ext.imp | notes |
|---|---|---|---|---|---|---|---|---|---|
(one row per status=target; S/A/R from smart_*; notes = assessment_notes.
M and T are in the Target scorecard above — has KPI and end year.)

## Enablers
(training / data infrastructure / R&D / … commitments, grouped by enabler_topic)

## What changed since last snapshot
(diff.py output — including the Target movements section — or
"first snapshot — baseline established")

## Notable trajectory
(2–4 sentences: direction of travel, gaps, restatements, target vs actual)

## Sources
(every report used, with URL and year)
```

Alongside the markdown report, step 10 emits `reports/<date>.xlsx` — a colored
Data + Legend workbook rendered from the snapshot for sharing.

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
- `scripts/export_xlsx.py` — render a snapshot CSV into a formatted `.xlsx`
  (Data + Legend tabs, colored); requires `openpyxl` (`pip install openpyxl`).
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
