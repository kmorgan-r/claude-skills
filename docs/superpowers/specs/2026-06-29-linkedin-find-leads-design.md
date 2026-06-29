# linkedin-find-leads — Design Spec

**Date:** 2026-06-29
**Status:** Approved for planning
**Author:** brainstormed with Claude

## Summary

A new skill, `linkedin-find-leads`, plus a new repo script `linkedin_lead_finder.py`,
that sources B2B cold leads **natively from LinkedIn** (via the ConnectSafely API) and
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

## Live-DB verification (2026-06-29, via climatepoint-odoo MCP)

`mailing.contact` schema and counts were confirmed against the live database with
`fields_get`, `read_group`, and `search_count`. Findings that shaped this design:

- **`x_persona` is a `selection`**, not free text. Allowed keys (exact):
  `sustainability`, `product_rd`, `ops_sc`, `founder_exec`, `investor`, `marketing`,
  `technical`, `partner`, `low_fit`, `unknown`. Writing any other value fails.
- **`x_seniority` is a `selection`** (5 keys): `analyst`, `manager`, `director`
  (label "Director / Head"), `vp` (label "VP / Director"), `c_level`.
- **`x_lead_score` is an integer**, labeled "Lead Score (1-10)" → emit an int 1–10.
- **`x_summary` (text)** exists — the natural home for the LinkedIn *about* blurb.
- Other useful real fields: `x_industry`, `x_department_function` (char),
  `x_company_size_id` (m2o).
- **Live counts:** `New` 555, unset 500 (eligible = **1,055**); `Attempting contact`
  2,893; `Connected` 2,011; `Closed` 7. Total 5,966. Contacts carrying a LinkedIn URL
  (the dedup pool) = **3,170**.

The `linkedin-outreach-odoo` reference doc's older counts (356 eligible, ~3,800 with URL)
are stale; trust the live numbers above and re-run discovery on first use.

## Architecture & data flow

```
1. Agent → MCP search_read  : pull ALL existing x_linkedin_url from mailing.contact
                              → normalize to slugs → dedup exclude-file (%TEMP%)
2. Script (3 source modes)  : search_people | org_followers | group_members/event_attendees
                              → raw people; dedup vs exclude-file + within-batch
3. Cheap ICP pre-filter     : score on headline/title/company/geo keywords (NO API)
                              → keep survivors over threshold; rest → Rejected sheet
4. Enrich (survivors only)  : get_profile, HARD-capped ≤120/day, checkpoint-resume
                              → about / experience / skills / company
5. Classify (reuse)         : existing climatepoint-contact-intelligence rubric
                              → persona (fixed key) / seniority (fixed key) / need_state
                                / lead_score (1-10) / outreach_angle
6. Workbook out             : Leads + Rejected + Run Config sheets, odoo_ready gate (%TEMP%)
7. Human review → offer MCP create (two-step confirm) : insert New mailing.contact rows
```

**Load-bearing ordering:** dedup → cheap pre-filter → enrich. The `get_profile` enrich
budget (~120/day) is the scarce resource; it is never spent on a profile that is a
duplicate of an existing Odoo contact or that fails the cheap ICP filter.

**Handoff model (decided):** hybrid. The skill **always** writes the reviewable workbook
to `%TEMP%`. After the human eyeballs it and marks `odoo_ready=yes`, the skill **offers**
to perform the Odoo `create` itself via the climatepoint-odoo MCP (gated by the MCP's
two-step confirmation code). The human review gate is never skipped.

## Components

Each unit has one purpose, a defined interface, and is testable in isolation.

| Unit | Where | Job | Depends on |
|---|---|---|---|
| Slug exclude-file builder | agent (MCP) | pull all `x_linkedin_url` → normalize slug → write exclude set to `%TEMP%` | climatepoint-odoo MCP `search_read` |
| `linkedin_lead_finder.py` | repo script | 3 source modes + dedup + cheap pre-score → stage-1 CSV | `connectsafely.py`, exclude-file |
| Cheap ICP scorer | in script | keyword score on search fields, no API | rubric keyword lists |
| Enrich stage | in script | `get_profile` on survivors, ≤120/day cap + checkpoint | ConnectSafely |
| Classifier handoff | agent | feed enriched CSV to existing climatepoint classifier | contact-intelligence skill |
| Workbook writer | in script | Leads/Rejected/Run Config sheets, `odoo_ready` col | openpyxl (find-cold-leads pattern) |
| MCP import | agent | gated `create` of `mailing.contact` rows | climatepoint-odoo MCP `create` |

### Source modes (script args)

- `--mode people --keywords "..." [--filters geo/title/industry]` → `search_people`
- `--mode org-followers --company-id <id>` → `org_followers` (resolve id via `search_companies`)
- `--mode group --group-id <id>` → `group_members`
- `--mode event --event-id <id>` → `event_attendees`

(Post-engager sources — `post_comments`/`post_reactions`, the warmest tier — are an
explicit future addition, not v1.)

### Slug normalization (the dedup key)

Reuse the exact parse from `linkedin-outreach-odoo` so both channels agree on identity:

```
url.split("/in/")[-1].rstrip("/").split("?")[0].split("/")[0]
```

Validate against `^[A-Za-z0-9._-]+$` (dots allowed — `firstname.lastname` slugs are
valid). Garbage URLs (company pages, malformed) fail validation → dropped, logged, never
forwarded.

## Field map → `mailing.contact`

Only fields verified present in the live schema. Selection fields must receive a valid
key (validated in-script before any MCP `create`).

| Odoo field | Type | Source | Note |
|---|---|---|---|
| `x_linkedin_url` | char | `profileUrl` | native key, always present |
| `first_name` / `last_name` | char | search | |
| `x_headline` | char | `headline` | |
| `x_job_title` | char | `currentPosition` → enrich `title` | |
| `company_name` | char | enrich `currentCompany` | |
| `x_summary` | text | enrich *about* | untrusted free text |
| `x_seniority` | selection (5) | classifier (derived from title/experience) | map to fixed key; `get_profile` returns no normalized seniority field |
| `x_industry` / `x_department_function` | char | enrich | optional |
| `country_id` | m2o | `location` | low-confidence (Apollo/LinkedIn mis-tag known) |
| `x_persona` | selection (10) | classifier | **must be a fixed key** |
| `x_need_state` | char | classifier | free text |
| `x_lead_score` | int (1–10) | classifier | |
| `x_outreach_angle` | text | classifier | untrusted free text |
| `x_lead_status` | char | literal `"New"` | makes the lead eligible for outreach |
| `email` | char | blank | LinkedIn-only until a later Apollo enrich pass |

## Rate limits

| Endpoint | Limit | Design response |
|---|---|---|
| `search_people` / `org_followers` / `search_companies` | server-throttled, no profile cap | source freely; paginate via `start` |
| `group_members` | ~1,000/day | one group per run is fine |
| `event_attendees` | per-event | one event per run |
| `get_profile` (enrich) | ~120/day | hard-cap in script; enrich only post-filter survivors; checkpoint-resume across days |

The script maintains a daily enrich counter, refuses to exceed the cap, and writes a
resume checkpoint (which slugs are already enriched) so a >120-lead batch spans multiple
days without re-spending budget.

## Safety & security

- **No sends.** The script has no `connect`/`message`/`follow` capability. Sending is
  exclusively `/linkedin-outreach-odoo`.
- **Dedup before enrich and before output.** Exclude-file built from the live Odoo slug
  set (3,170); plus within-batch dedup. Protects both the enrich budget and the
  weekly connect cap (no duplicate contacts → no duplicate sends).
- **Untrusted free text.** LinkedIn `headline`/`about` are self-authored and
  attacker-influençable. They flow into `x_summary` / `x_outreach_angle`, which
  `/linkedin-outreach-odoo` **already** treats as unsanitized (personalized vs fallback
  mode, ≤120-char bounded hook, runtime screening). This skill therefore introduces **no
  new injection surface** — it labels the LinkedIn-sourced `x_outreach_angle` as
  unsanitized at import, identical to the Apollo path. The *about* text fed to the
  classifier is screened (as data, not instructions) before classification.
- **Selection-value enforcement.** `x_persona` and `x_seniority` values are validated
  against their fixed key sets before MCP `create`; an invalid key fails fast, never
  writes silently.
- **PII stays local.** Working dir `%TEMP%\linkedin-find-leads\`, outside any git tree.
  A committed `.gitignore` in the skill dir backstops against override into a tracked
  path (`*_leads_*.csv`, workbook). Never `git add -f`; never paste rows into commits/PRs.
- **MCP `create` is gated.** Two-step confirmation code; the human reviews
  `odoo_ready=yes` rows before any DB write. Same discipline as the outreach write-back.

## Testing

Mirror `find-cold-leads/scripts/test_lead_crawler.py` (pytest, no live API):

- **Unit:** slug normalize/validate (including garbage URLs), dedup logic, cheap-score
  rubric, enrich cap enforcement + checkpoint resume, CSV/RFC-4180 escaping +
  spreadsheet-formula-injection tab-prefix, selection-key validation.
- **Fixtures:** canned `search_people` / `org_followers` / `group_members` /
  `event_attendees` / `get_profile` JSON responses — no network in tests.
- **Eval (optional):** mirror `find-cold-leads/evals/` with a qualification gold-set for
  cheap-pre-filter precision.

## Reuse / integration points

- `connectsafely.py` — the ConnectSafely API client. Already wraps `search_people`,
  `search_companies`, `org_followers`, `group_members`, `event_attendees`, `get_profile`.
- `linkedin_research.py` — existing search+enrich+CSV precedent; the new script follows
  its shape but adds Odoo dedup, the cheap pre-filter, the enrich cap/checkpoint, and the
  workbook output.
- `climatepoint-contact-intelligence` skill — the persona/need/score/angle classifier;
  reused, not duplicated. Its persona output must be constrained to the 10 valid
  `x_persona` keys and its score to 1–10.
- `/find-cold-leads` workbook pattern (`LEAD_COLUMNS`, Leads/Rejected/Run Config sheets,
  `odoo_ready` gate) — the output workbook mirrors it for consistency.
- `/linkedin-outreach-odoo` — the downstream consumer; this skill's output is its input.

## Out of scope (explicit future hooks)

- Post-engager sourcing (`post_comments` / `post_reactions`) and `profile_visitors` —
  the warmest tiers. Add as new `--mode` values later.
- Apollo email enrichment of LinkedIn-sourced leads (dual-channel). A later pass mapping
  `apollo_people_match` onto the existing rows to populate `email`.
- Multi-touch LinkedIn nurture, inbox/reply triage — separate skills (brainstorm
  directions C/D), not this one.
