# Free (and cheap-paid) ESG / CSR data source catalog

Read this when choosing where to pull from for a given company + domain. Coverage,
access method, and which domains each source is strong/weak on. Verify endpoints and
free tiers before depending on them — these shift.

## Entity resolution — do this first when scaling

To match a company across years (and across sources), anchor it to a stable ID. Use
the **LEI** from GLEIF.

- **GLEIF API** — free, no key.
  `https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=Royal%20Philips`
  Returns the LEI + canonical legal name + jurisdiction. Put the LEI in the snapshot
  `lei` column. Names drift (rebrands, mergers); the LEI doesn't.

## Free structured sources (prefer these for breadth — cheap to pull at scale)

| Source | Access | Coverage | Domains | History | Notes |
|---|---|---|---|---|---|
| **WikiRate API** | REST, free key | thousands, uneven | any (flexible metrics) | yes | Primary open structured source. Crowd/NGO-sourced metrics. |
| **SBTi** | downloadable CSV/Excel, free | ~7k+ companies | climate (validated targets) | yes (target & baseline yrs) | Best free climate-target list. Near/net-zero status. |
| **SEC EDGAR** | full-text search API, free | US filers (~8k) | mixed (in filings) | yes | `https://efts.sec.gov/LATEST/search-index?q=...`. US only. |
| **Net Zero Tracker** | downloadable, free | companies + states | climate commitments | yes | Net-zero pledge status + integrity flags. |
| **CDP** | partial open data | ~20k+ disclose | climate/water/forests | yes | Full dataset gated; some open slices. |
| **TNFD adopters list** | downloadable, free | early adopters | biodiversity/nature | growing | Who has committed to TNFD-aligned reporting. |
| **yfinance** | python lib, free | listed equities, thin | ESG score (Sustainalytics-derived) | limited | Spotty since provider restrictions. Score only, not raw metrics. |

## Free unstructured source — report PDFs (this skill's find→fetch→extract chain)

The fallback for anything the structured sources miss (most circular-economy and
biodiversity detail). Use `scripts/find_reports.py` → `fetch_pdf.py` → `extract_pdf.py`.

| Source | Access | Coverage | Notes |
|---|---|---|---|
| **Company IR / sustainability archive** | scrape | per company, deep | Most reliable for one known company. Reports often back 10+ yrs. |
| **sustainabilityreports.com** | scrape | claims 200k+ reports | Largest free repo. No clean public API; scrape listings. |
| **responsibilityreports.com** | scrape | ~4.5k companies / ~26k reports | Smaller. No API. |
| **DuckDuckGo** (`ddgs` lib) | free, no key | the web | `"<company>" sustainability report <year> filetype:pdf`. |

PDF read tools (all free, no key): **PyMuPDF** (`fitz`) for text, **pdfplumber** for
tables, **Docling** (IBM, open source) when tables are messy. Jina Reader
(`r.jina.ai/<url>`) reads PDFs too but needs a key on flagged networks.

## Paid sources — only when free coverage runs out (add as another `source`)

The schema and diff don't change; a paid feed is just another `source` value. Reach
for these to push a universe toward tens of thousands or to get clean global
structured metrics.

| Source | Cheapest ESG tier | Coverage | Notes |
|---|---|---|---|
| **Financial Modeling Prep (FMP)** | Premium ~$59/mo | US-listed (~8k) | `/stable/esg-disclosures`, `/esg-ratings`, `/esg-benchmark`. **US only**, and gives composite E/S/G **scores**, not Scope 1/2/3 or circular/biodiversity detail. `esg-disclosures` returns dated rows = built-in history. Free/Starter tiers return "Restricted Endpoint". |
| **ESG Book** | commercial | ~50k, global | API-first, broad. |
| **CSRHub** | commercial | ~50k, aggregates 970+ sources | The "36k companies" feel. |
| **Refinitiv/LSEG, MSCI, Sustainalytics, S&P, Bloomberg** | enterprise | 12–15k+ | Deep but expensive. |
| **RepRisk** | commercial | broad | Controversy / incident data. |

## Coverage reality by domain (free stack)

- **Climate** — strongest. SBTi + CDP + Net Zero Tracker + reports.
- **Social / governance** — decent. Reports + WikiRate + EDGAR.
- **Circular economy** — thin. Mostly PDF extraction from reports.
- **Biodiversity / nature** — thinnest, newest. TNFD list + reports.

A clean free universe realistically reaches ~5–15k companies with usable data, not
36k. Go broader by bolting on a paid adapter later — same schema, same diff.
