# linkedin-find-leads — Design Spec

**Date:** 2026-06-29
**Status:** Approved for planning (review findings applied 2026-06-29)
**Author:** brainstormed with Claude

## Summary

A new skill, `linkedin-find-leads`, plus a script `linkedin_lead_finder.py`, that
sources B2B cold leads **natively from LinkedIn** (via the ConnectSafely API) and
delivers them as Odoo-ready `mailing.contact` rows. It is the missing **front-end** for
the LinkedIn outreach channel: today, leads enter Odoo only through `/find-cold-leads`
(Apollo → email + LinkedIn URL). This skill adds a LinkedIn-native sourcing path that
hands `x_linkedin_url` (the connect target) directly, drops leads into `mailing.contact`
as `New`, where the existing `/linkedin-outreach-odoo` skill already picks them up and
sends connection requests.

### Why this exists (and what it is NOT)

The point is **warmer, better-targeted leads — not raw volume.** Verified against the
live Odoo DB (2026-06-29): the eligible pool (`x_lead_status` = `New` or unset) is
**1,055 contacts** — roughly 11+ weeks of runway at the 90-connections/week cap. There
is no shortage of leads. The value of LinkedIn-native sourcing is **intent and ABM
targeting** that open-web scraping cannot see: people in a specific group, attendees of
a specific event, followers of a specific competitor.

Hard boundaries — this skill does **NOT**:
- **Send anything.** Sourcing only. Connection requests stay in `/linkedin-outreach-odoo`.
  The script imports no `connect`/`message` capability.
- **Find email.** LinkedIn-native leads land with `email` blank. Email enrichment (Apollo
  `apollo_people_match`) is an explicit out-of-scope hook for a later pass, not v1.
- **Scrape the open web.** That is `/find-cold-leads`.

### Packaging & repository

The skill is **self-contained inside the `claude-skills` repo** at `linkedin-find-leads/`
(SKILL.md + `references/` + `scripts/` + `evals/` + `.gitignore`), mirroring the
`find-cold-leads` layout. The shared ConnectSafely client `connectsafely.py` lives in
`~\marketing\` (NOT in this repo). The script reaches it exactly as `/linkedin-outreach-odoo`
documents: insert the marketing dir onto `sys.path` before importing. See
**Client import & pre-flight** below — this is load-bearing for both runtime and tests.

## Live-DB verification (2026-06-29, via climatepoint-odoo MCP)

`mailing.contact` schema and counts were confirmed against the live database with
`fields_get`, `read_group`, and `search_count`. Findings that shaped this design:

- **`x_persona` is a `selection`**, not free text. Allowed keys (exact):
  `sustainability`, `product_rd`, `ops_sc`, `founder_exec`, `investor`, `marketing`,
  `technical`, `partner`, `low_fit`, `unknown`. Writing any other value fails. Note the
  `unknown` key exists — it is the per-row fallback for an unmappable persona.
- **`x_seniority` is a `selection`** (5 keys): `analyst`, `manager`, `director`
  (label "Director / Head"), `vp` (label "VP / Director"), `c_level`. **There is no
  `unknown` seniority key** — an unmappable title must leave `x_seniority` unset (omitted
  from the create payload), never an invalid key. Labels differ from keys — validate and
  write the **key**, never the label text.
- **`x_lead_score` is an integer**, labeled "Lead Score (1-10)" → emit an int 1–10.
- **`x_summary` (text)** exists — the natural home for the LinkedIn *about* blurb.
- Other real fields seen: `x_industry`, `x_department_function` (char),
  `x_company_size_id` (m2o), `x_headline`, `x_job_title`, `x_need_state`,
  `x_outreach_angle`.
- **Live counts:** `New` 555, unset 500 (eligible = **1,055**); `Attempting contact`
  2,893; `Connected` 2,011; `Closed` 7. Total 5,966. Contacts carrying a LinkedIn URL
  (the dedup pool) = **3,170**.

**Re-verify before implementation.** The script MUST run `fields_get` on `mailing.contact`
at first use and **fail fast in-script** if any field it intends to write
(`x_headline`, `x_job_title`, `company_name`, `x_summary`, `x_seniority`, `x_persona`,
`x_need_state`, `x_outreach_angle`, `x_lead_score`, `x_lead_status`, `x_linkedin_url`,
`first_name`, `last_name`; plus optional `x_industry`/`x_department_function`/`country_id`)
is absent — a missing field must surface as a clear error, never a silent bad `create`.
The `linkedin-outreach-odoo` reference doc's older counts (356 eligible, ~3,800 with URL)
are stale; trust the live numbers above and re-run discovery on first use.

## Client import & pre-flight (load-bearing)

`connectsafely.py` instantiates a module-level client at import (`cs = ConnectSafely()`),
whose constructor calls **`sys.exit()` if `CONNECTSAFELY_API_KEY` is unset** — an abrupt
process exit, not a catchable exception. Three consequences the design must handle:

1. **Runtime / cross-repo import.** The script inserts the marketing dir onto `sys.path`,
   then imports the client through a **lazy accessor** (`get_client()` that constructs/
   caches the client on first call) rather than importing the module-level `cs` at module
   top. This keeps a bare `import linkedin_lead_finder` side-effect-free.
2. **Pre-flight check — eager guarded construction.** Before any sourcing or budget spend,
   the script verifies `CONNECTSAFELY_API_KEY` is present (boolean presence test — never echo
   the value) and the climatepoint-odoo MCP is reachable, then **eagerly constructs the client
   once** via `get_client()` inside a guard that traps the constructor's `SystemExit` and
   converts it to a clear actionable message (`set CONNECTSAFELY_API_KEY`). Because the client
   is cached after this single guarded construction, every later `get_client()` call (the first
   real one is `search_companies` in `--mode org-followers`, before any enrich) returns the
   cached instance and **never re-enters the constructor** — so a key cleared mid-run cannot
   trigger an un-guarded `sys.exit()`. A bare presence test alone would leave a TOCTOU window;
   eager guarded construction is what makes "fails cleanly up front" actually hold.
3. **Testability.** Because construction is lazy + cached, `import linkedin_lead_finder` succeeds
   with the key unset. Tests stub `get_client()` (or the individual `cs.*` methods) and
   never touch the network. An explicit test asserts the module imports key-unset.

## Architecture & data flow

```
1. Agent → MCP search_read  : pull ALL existing x_linkedin_url from mailing.contact
                              → normalize+LOWERCASE to slugs → dedup exclude-set (%TEMP%)
                              → log drop count; secondary key for malformed stored URLs
2. Script (4 source modes)  : search_people | org_followers | group_members | event_attendees
                              → SOURCING FULLY COMPLETES across all modes/pages first
                              → global within-batch dedup (union) + dedup vs exclude-set
3. Cheap ICP pre-filter     : score on headline/title/company/geo keywords (NO API)
                              → keep survivors with score >= threshold; rest → Rejected sheet
4. Enrich (survivors only)  : get_profile, HARD-capped by live quota, atomic checkpoint-resume
                              → aboutText / experience[0].title / topSkills / currentCompany
5. Classify (reuse)         : existing climatepoint-contact-intelligence rubric
                              → persona (fixed key, default unknown) / seniority (fixed key
                                or unset) / need_state / lead_score (1-10) / outreach_angle
                              → rows with invalid persona keys coerced at classify time
6. Workbook out             : Leads + Rejected + Run Config sheets, odoo_ready gate (%TEMP%)
                              → ALL string cells formula-injection-neutralized
7. Human review → offer MCP create (two-step confirm) : insert New mailing.contact rows
                              → per-row create-back marker for idempotent resume
```

**Load-bearing ordering:** sourcing → global dedup → cheap pre-filter → enrich. The
`get_profile` enrich budget is the scarce resource; it is never spent on a profile that is
a duplicate (of an existing Odoo contact OR of another row already sourced this run) or
that fails the cheap ICP filter.

### Step 1 — exclude-set build (dedup correctness)

- Apply the **identical** normalize+lowercase used on sourced URLs (see Slug normalization)
  to every existing Odoo `x_linkedin_url`, so the two sides compare equal.
- Log the count of stored URLs dropped by validation; if the drop rate is unusually high,
  **warn** — it means dedup coverage is degraded (legacy malformed URLs).
- For a stored URL that fails slug validation but is non-empty, fall back to a secondary
  dedup key (normalized full URL, or first/last+company) so a malformed-but-real existing
  contact still suppresses a freshly-sourced duplicate.

### Step 2 — sourcing & global dedup (budget protection)

- All sourcing (every `--mode` and every page of `search_people`) **completes first**; the
  within-batch dedup set is finalized as a **union across all modes/pages** before the
  enrich stage begins. The enrich loop iterates the deduped survivor list only — no
  per-page/per-mode enrich that could enrich the same person twice.
- Defensive parsing: a missing/empty `people` key (or `followers`/`members`/`attendees`)
  is treated as zero results, not a `KeyError`. Null optional fields → blank.
- **Confirm item field names at implementation.** The `search_people` item keys this design
  reads (`headline`, `currentPosition`, `firstName`/`lastName`, `profileUrl`, `location`) and
  the response wrapper keys (`people`/`followers`/`members`/`attendees`) are not all pinned in
  the client source — confirm them against a live/fixture response (the same discipline as the
  Odoo `fields_get` re-verify). The cheap ICP scorer must **tolerate a missing title key**
  (fall back to `headline`) so a renamed/absent `currentPosition` does not silently zero every
  title-based score and push everything to the enrich fallback.

### Step 3 — cheap ICP pre-filter

- Comparator is explicit: keep rows with `score >= threshold`. A score of 0 (no keyword
  match) is below any positive threshold → routed to the Rejected sheet and **excluded
  from the enrich call set**. Boundary (`== threshold`) is kept. Tested at/above/below.

### Step 4 — enrich (the scarce stage)

Each `get_profile` is wrapped; the loop discriminates failure modes and persists state
incrementally:

- **Field extraction uses the real response shape:** `aboutText` (→ `x_summary`),
  `experience[0].title` (most-recent role → `x_job_title` fallback), `currentCompany`
  (→ `company_name`), `topSkills[]`, `experience[].companyName`. There is **no** top-level
  `title`, `about`, `industry`, or `department_function` — do not read them.
- **Failure discrimination — two exception classes.** The wrapper catches **both**
  `ConnectSafelyError` (fires only on non-2xx) **and** response-parse exceptions
  (`JSONDecodeError`/`KeyError`/`TypeError` on a 2xx body — a 200-with-garbage response is
  NOT a `ConnectSafelyError`). On **429 / cap reached** (a `ConnectSafelyError`) → stop
  enriching for the day, persist the checkpoint, emit the cap-reached message (below). On
  **transient 5xx / timeout** → mark that one slug failed (best-effort, like
  `linkedin_research.py`), do **not** mark it done, continue; a single bounded retry with
  backoff is allowed for transient only, never for the cap. On **malformed/parse failure**
  (deterministic) → skip-and-continue (not done), **no retry** (a retry just burns another
  quota unit on the same guaranteed failure). No failure path escapes the per-row `try` — a
  single bad slug never aborts the batch.
- **Live-quota reconciliation (sole authoritative hard stop):** after each call read
  `cs.last_rate.remaining` and stop when it reaches a safety floor (e.g. ≤5). The shared
  account quota (other tools spend it too) makes this post-call header the **only** hard
  stop. The local daily counter is **advisory** — it may warn/inform but must **never by
  itself** produce a no-call state, otherwise the system can deadlock (counter reads "full" →
  no call made → no fresh `cs.last_rate` → counter never clears). At least one call always
  proceeds so a fresh header is read and the true window/remaining is reconciled.
- **"Day" boundary:** the counter resets on the **server** window, not local midnight.
  Persist `cs.last_rate.reset` in the checkpoint and zero the counter once that timestamp
  passes. On resume: if `now ≥ persisted reset` → zero the counter; **else still permit a
  probe call** (the live floor read post-call is authoritative) rather than blocking on the
  stale counter. **Initial state:** absent any persisted `reset` (brand-new run, no call yet)
  → counter starts at 0 and the first call proceeds; `reset` is established from its header.
- **Atomic checkpoint:** after **each successful** `get_profile`, append the slug (and last
  seen remaining/reset) to the checkpoint via temp-file write + rename, before moving on.
  The daily counter is derived from the persisted checkpoint, not an in-memory variable, so
  an interrupted run (Ctrl-C, crash, OOM) never re-spends already-consumed calls and never
  re-enriches a done slug the next day. Failed (non-cap) calls are NOT checkpointed as done.
- **Cap-reached UX:** emit `enrich cap reached — M of N enriched, resume tomorrow`, leave
  the partial workbook clearly marked incomplete (un-enriched survivors flagged pending,
  `odoo_ready` default no), and have resume continue from the checkpoint **without re-running
  discovery**.

### Step 5 — classify (fixed-key enforcement at the boundary)

- Persona/seniority/score come from the reused classifier, validated against the **exact
  key sets** (not labels) at classify time:
  - Invalid/absent **persona** → coerce to `unknown` (a valid key); the row stays.
  - Unmappable **seniority** → leave `x_seniority` **unset** (omit from create). The row
    **stays on the Leads sheet** with a review-annotation flag (`seniority_unset=yes`); it is
    NOT moved to the Rejected sheet (which is reserved strictly for rows excluded from the
    Odoo create set). Never write an invalid key, never abort the batch.
  - A documented title→seniority derivation maps common titles to the 5 keys; titles that
    map to nothing → unset (above).
- This catches a bad key at classify time (before the workbook is presented), not as a late
  hard-fail at MCP-write time.

### Step 7 — MCP create (idempotent, gated)

- The skill **always** writes the reviewable workbook to `%TEMP%`. After the human marks
  `odoo_ready=yes`, the skill **offers** to `create` the rows via the climatepoint-odoo MCP
  (gated by the MCP's server-generated two-step confirmation code; never fabricated).
- **Per-row idempotency (mandatory pre-create re-query):** immediately before the create
  loop, **re-query the current Odoo slug set** and skip any survivor already present. This is
  a **required** step, not an alternative — it is the airtight backstop and also fixes two
  adjacent problems at once: (a) the start-of-run exclude-set predates this run's own inserts,
  and (b) on a multi-day capped batch the day-1 exclude-set is stale by day-2 (another tool,
  `/find-cold-leads`, or a prior partial create may have inserted overlapping contacts). Note:
  "resume continues without re-running discovery" does NOT waive this create-time re-check.
  As a secondary aid, create row-by-row and write the new Odoo record id / `created=yes` back
  to the workbook row on each success (durably — a side-car marker or per-row flush, since the
  enrich checkpoint's atomic-write guarantee does not extend to the openpyxl workbook); but
  correctness rests on the mandatory re-query, not on workbook-marker durability.
- **Completion invariant:** `odoo_ready` MUST NOT be accepted on any survivor still flagged
  `pending` (un-enriched) — the create step skips pending rows by construction, so a
  cap-interrupted run can never offer create on half-classified rows. Authoritative artifact
  per resume phase: the **enrich checkpoint** governs enrich resume; the **create-time
  re-queried Odoo slug set** governs create resume — the two never disagree.
- `country_id` resolution is **best-effort**: resolve the free-text `location` to a
  `res.country` id; on no-match, **omit the field** from the create payload (never write
  `false`, never index a `false` m2o). Included in the pre-create validation pass.

## Components

Each unit has one purpose, a defined interface, and is testable in isolation.

| Unit | Where | Job | Depends on |
|---|---|---|---|
| Slug exclude-set builder | agent (MCP) | pull all `x_linkedin_url` → normalize+lowercase → exclude set to `%TEMP%` (+ drop log) | climatepoint-odoo MCP `search_read` |
| `linkedin_lead_finder.py` | `linkedin-find-leads/scripts/` | 4 source modes + global dedup + cheap pre-score + capped enrich → stage CSV/workbook | `connectsafely.py` (via `sys.path` + lazy `get_client()`), exclude-set |
| Cheap ICP scorer | in script | keyword score on search fields, no API | rubric keyword lists |
| Enrich stage | in script | `get_profile` on survivors, live-quota cap + atomic checkpoint + cap/transient discrimination | ConnectSafely client |
| Classifier handoff | agent | feed enriched CSV to existing climatepoint classifier; coerce/validate fixed keys | contact-intelligence skill |
| Workbook writer | in script | Leads/Rejected/Run Config sheets, `odoo_ready` col, formula-injection guard on ALL cells | openpyxl (find-cold-leads pattern) |
| MCP import | agent | gated, per-row idempotent `create` of `mailing.contact` rows | climatepoint-odoo MCP `create` |

Test file sits beside the script: `linkedin-find-leads/scripts/test_linkedin_lead_finder.py`
(mirroring `find-cold-leads/scripts/test_lead_crawler.py`). The agent/MCP units
(exclude-set build, MCP create) are covered by the human-gated workflow, not pytest.

### Source modes (script args)

- `--mode people --keywords "..." [--filters geo/title/industry]` → `search_people`
  (the **only** paginating mode: loops `start` until an empty/short page; terminates).
- `--mode org-followers --company-id <id>` → `org_followers` (resolve id via
  `search_companies` first). **Single call, no pagination.**
- `--mode group --group-id <id>` → `group_members`. **Single call, no pagination.**
- `--mode event --event-id <id>` → `event_attendees`. **Single call, no pagination.**

(Post-engager sources — `post_comments`/`post_reactions`, the warmest tier — are an
explicit future addition, not v1.)

### Slug normalization (the dedup key)

Extend the `linkedin-outreach-odoo` parse so both channels agree on identity:

```
url.split("/in/")[-1].rstrip("/").split("?")[0].split("/")[0]   then .lower()
```

Rules:
- **Require `/in/` in the URL** before parsing; otherwise drop + log (do not rely on the
  accidental colon-rejection — a scheme-less `linkedin.com/company/acme` would otherwise
  parse to `linkedin.com`, which passes the validator). Known non-person paths
  (`/company/`, `/school/`, `/showcase/`) are rejected.
- **Lowercase** the slug before use as a dedup key, on BOTH the exclude-set side and the
  sourced side (LinkedIn slugs are case-insensitive identities; `John-Doe` and `john-doe`
  are the same person).
- Validate against `^[A-Za-z0-9._-]+$` (dots allowed — `firstname.lastname` slugs are
  valid). **Non-ASCII decision:** v1 drops Unicode slugs and logs them (documented
  limitation); revisit with `\w`+`re.UNICODE` if it proves lossy.

## Field map → `mailing.contact`

Only fields verified present in the live schema (re-checked via `fields_get` at runtime).
Selection fields must receive a valid key (validated in-script before any MCP `create`).

| Odoo field | Type | Source | Note |
|---|---|---|---|
| `x_linkedin_url` | char | `profileUrl` | native key, always present |
| `first_name` / `last_name` | char | search item | |
| `x_headline` | char | search `headline` | untrusted free text |
| `x_job_title` | char | search `currentPosition` (fallback enrich `experience[0].title`) | no top-level `title` in `get_profile` |
| `company_name` | char | enrich `currentCompany` | NOT `x_company` |
| `x_summary` | text | enrich `aboutText` | untrusted free text (field is `aboutText`, not `about`) |
| `x_seniority` | selection (5) | classifier (derived from title/experience) | no `unknown` key — **unmappable → omit field**; write key not label |
| `x_industry` / `x_department_function` | char | **classifier-derived** (optional) | `get_profile` returns neither — do not read from enrich; may be left blank in v1 |
| `country_id` | m2o | resolve `location` → `res.country` id | best-effort; **omit on no-match**, never write `false` |
| `x_persona` | selection (10) | classifier | **must be a fixed key**; invalid → coerce to `unknown` |
| `x_need_state` | char | classifier | free text |
| `x_lead_score` | int (1–10) | classifier | |
| `x_outreach_angle` | text | classifier | untrusted free text |
| `x_lead_status` | char | literal `"New"` | makes the lead eligible for outreach |
| `email` | char | blank | LinkedIn-only until a later Apollo enrich pass |

## Rate limits

| Endpoint | Limit | Design response |
|---|---|---|
| `search_people` | server-throttled, no profile cap | paginate via `start` until empty/short page; terminate |
| `org_followers` / `group_members` / `event_attendees` / `search_companies` | server-throttled | **single call each, no pagination param** — take what one response returns |
| `get_profile` (enrich) | ~120/day, **shared across all tools on the account** | live-quota floor (`cs.last_rate.remaining` ≤ floor → stop) is the **sole authoritative hard stop**; the checkpoint-derived local counter is **advisory only** and never by itself blocks a call; reset on server `cs.last_rate.reset`, not local midnight; atomic per-profile checkpoint; cap (429) stops the day, transient errors retry-once-then-skip, parse failures skip (no retry) |

The script maintains a checkpoint-derived **advisory** daily counter and writes a resume
checkpoint (slugs already enriched + last seen remaining/reset) so a >120-lead batch spans
multiple days without re-spending budget or re-running discovery. The post-call live floor is
the only condition that hard-stops the day; the advisory counter must never deadlock the loop.

## Safety & security

- **No sends.** The script has no `connect`/`message`/`follow` capability. Sending is
  exclusively `/linkedin-outreach-odoo`.
- **Dedup before enrich and before output.** Exclude-set built from the live Odoo slug set
  (3,170, normalized+lowercased); plus global within-batch dedup. Protects both the enrich
  budget and the weekly connect cap (no duplicate contacts → no duplicate sends).
- **Untrusted free text.** LinkedIn `headline`/`aboutText`/`location`/names are
  self-authored and attacker-influenceable. They flow into `x_summary` / `x_outreach_angle`
  / `x_headline`, which `/linkedin-outreach-odoo` **already** treats as unsanitized
  (personalized vs fallback mode, ≤120-char bounded hook, runtime screening). This skill
  introduces **no new injection surface** — it labels LinkedIn-sourced text as unsanitized
  at import, identical to the Apollo path. The *about* text fed to the classifier is screened
  (as data, not instructions) before classification. **`ConnectSafelyError` messages embed
  the first 800 chars of the raw (untrusted) API response body** — treat as untrusted
  before logging anywhere.
- **Formula-injection: ALL cells.** The neutralization (tab-prefix / RFC-4180 escaping)
  applies to **every string cell on every sheet** — Leads, Rejected reasons, Run Config,
  and any error/diagnostic column — not just classifier output. Covers the openpyxl workbook
  (the primary deliverable) AND any CSV. Cells beginning `=` `+` `-` `@` or tab/CR/LF are
  neutralized.
- **Selection-value enforcement (per-row).** `x_persona`/`x_seniority` validated against
  their fixed key sets before MCP `create`; invalid persona → `unknown`, unmappable
  seniority → omitted; a single bad row is flagged for review, never aborts the batch.
- **PII stays local.** Working dir `%TEMP%\linkedin-find-leads\`, outside any git tree.
  A committed `.gitignore` in the skill dir backstops against override into a tracked path
  (`*_leads_*.csv`, workbook, checkpoint). Never `git add -f`; never paste rows into
  commits/PRs. Secrets read from env by the client/MCP — never echoed into a tool call.
- **MCP `create` is gated + idempotent.** Server-generated two-step confirmation code (never
  fabricated); the human reviews `odoo_ready=yes` rows before any DB write; per-row
  create-back markers make a partial-batch retry safe.

## Testing

Mirror `find-cold-leads/scripts/test_lead_crawler.py` (pytest, no live API):

- **Import safety:** `import linkedin_lead_finder` succeeds with `CONNECTSAFELY_API_KEY`
  unset (proves the lazy-client boundary; otherwise `connectsafely`'s import-time
  `sys.exit()` would kill collection).
- **Field extraction:** a `get_profile` fixture using the **real keys** (`aboutText`,
  `topSkills`, `experience[{title,companyName}]`, `currentCompany`) asserts
  `x_summary`/`x_job_title`/`company_name` populate, while `x_industry`/`x_department_function`/
  `x_seniority` are classifier-derived (not read from nonexistent fields). A wrong-key
  fixture must fail.
- **Slug normalize/validate:** dotted slug round-trip; `/company/`, `/school/`, scheme-less
  `linkedin.com/company/acme`, and no-`/in/` URLs dropped+logged; query strings stripped;
  trailing locale stripped; **case-fold** so exclude-set and sourced slug compare equal;
  Unicode-slug drop documented.
- **Dedup:** vs exclude-set (incl. case-mismatch), within-batch collapse across modes/pages.
  Mirror `test_dedupe_skips_linkedin_and_normalizes_domains`. **Malformed-stored-URL secondary
  key — assert end-to-end suppression** (not just that a secondary key is computed): seed the
  exclude-set with a stored URL that fails primary slug validation but resolves under the
  secondary key, source the same person, and assert that sourced row is **dropped and never
  reaches the enrich call set**.
- **Schema fail-fast:** stub `fields_get` to return a field set missing one required write
  field (e.g. drop `x_seniority`) → assert the script raises a clear error **before** any
  `create`/enrich spend; companion test where all required fields present → pre-flight passes.
- **Cheap score:** boundary at/above/below threshold; score-0 → Rejected and excluded from
  enrich set.
- **Enrich:** (a) ordering — `get_profile` called only for post-dedup, post-filter survivors
  (count + identity of stubbed calls); (b) resume — counter is **read from the on-disk
  checkpoint, not an in-memory variable**: a checkpoint whose `reset` has NOT passed keeps the
  persisted count (cap still enforced on run 2); a checkpoint whose `reset` HAS passed zeros
  it; an interrupted (temp-file) write leaves the prior checkpoint intact; only the not-yet-done
  remainder is enriched, total ≤ cap across two runs; (c) cap (429) stops the day and persists
  checkpoint; (d) transient 5xx retries once then skips one slug, continues (not counted done);
  (e) **parse failure on a 2xx body** (`JSONDecodeError`) skips the slug with NO retry and does
  not abort the batch; (f) live floor (`cs.last_rate.remaining` ≤ floor) hard-stops; the advisory
  counter alone never produces a no-call state (probe call always allowed after a window-boundary).
- **Selection keys:** parametrized — all 10 personas + all 5 seniorities pass; an invalid
  persona coerces to `unknown`; an unmappable seniority is omitted (not written); neither
  aborts the batch. Pin the exact key sets.
- **Per-mode dispatch:** each `--mode` invokes the correct client method; `org-followers`
  resolves the company id via `search_companies` first; `search_people` pagination halts on
  an empty page (no infinite loop); single-call modes make exactly one call.
- **Formula injection:** workbook (openpyxl) cells beginning `=`/`+`/`-`/`@`/tab/CR/LF are
  neutralized for ALL untrusted columns (`x_summary`, `x_outreach_angle`, `x_headline`,
  `location`, names) AND Rejected-reason/error columns. Mirror
  `test_write_table_neutralizes_formula_starters`. **Include the real injection vector:**
  populate the error cell from a simulated `ConnectSafelyError` whose (untrusted) body begins
  with `=`/`@` and assert it is neutralized — not just a hand-crafted string that never
  traversed the error path.
- **`country_id` omit-on-no-match:** matched location → id present in the create payload;
  unmatched location → assert the key is **absent** (`"country_id" not in payload`), never
  `payload["country_id"] is False`.
- **Fixtures:** canned `search_people` / `org_followers` / `group_members` /
  `event_attendees` / `get_profile` JSON responses — no network in tests.
- **Eval (optional):** mirror `find-cold-leads/evals/` with a qualification gold-set for
  cheap-pre-filter precision.

## Reuse / integration points

- `connectsafely.py` (in `~\marketing\`) — the ConnectSafely API client. Already wraps
  `search_people`, `search_companies`, `org_followers`, `group_members`, `event_attendees`,
  `get_profile`. Imported via `sys.path` insert + lazy `get_client()` (see Client import).
- `linkedin_research.py` — existing search+enrich+CSV precedent; the new script follows its
  shape but adds Odoo dedup, the cheap pre-filter, the live-quota enrich cap/atomic
  checkpoint, and the workbook output.
- `climatepoint-contact-intelligence` skill — the persona/need/score/angle classifier;
  reused, not duplicated. Output constrained to the 10 valid `x_persona` keys (invalid →
  `unknown`), the 5 `x_seniority` keys (unmappable → unset), and score 1–10.
- `/find-cold-leads` workbook pattern (`LEAD_COLUMNS`, Leads/Rejected/Run Config sheets,
  `odoo_ready` gate, formula-injection guard) — the output workbook mirrors it.
- `/linkedin-outreach-odoo` — the downstream consumer; this skill's output is its input;
  same `connectsafely.py`, same `sys.path` import pattern, same Odoo field conventions.

## Out of scope (explicit future hooks)

- Post-engager sourcing (`post_comments` / `post_reactions`) and `profile_visitors` —
  the warmest tiers. Add as new `--mode` values later.
- Apollo email enrichment of LinkedIn-sourced leads (dual-channel). A later pass mapping
  `apollo_people_match` onto the existing rows to populate `email`.
- Multi-touch LinkedIn nurture, inbox/reply triage — separate skills (brainstorm
  directions C/D), not this one.
