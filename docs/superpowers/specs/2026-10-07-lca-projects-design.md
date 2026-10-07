# LCA Projects: recall consulting sessions per Active client

Date: 2026-10-07
Status: approved in brainstorming, awaiting written-spec review

## 1. Purpose

Kevin runs LCA consulting work for clients through Claude Code sessions that call the
ClimatePoint MCP. The memory of that work is scattered today. The Acme Motors
session (`1937fdab`) ran from `builder2`, its memory file (`client-lca-state.md`) sits
in `backend2`'s memory folder, its working files sit loose in `Downloads\`, and its
transcript is 15 MB.

Build a way to:

1. notice, without being told, that a session is working on a ClimatePoint product,
2. keep a list of those client projects, filtered to companies marked **Active** on the
   platform (builder PR #4778, merged 2026-10-07, migration live),
3. show live platform follow-up/onboarding state per product,
4. start a fresh Claude session seeded with a per-project brief, in a new terminal tab.

### Success criteria

- A session that calls a ClimatePoint tool with a `product_id` belonging to an Active
  company shows that company in the band and the `/lca` pane, with no manual step.
- Opening `/lca` and picking a company opens a new Windows Terminal tab whose Claude
  session reads that company's brief and checks live follow-up state.
- `/lca-save` writes or updates the brief from the current conversation.
- A company whose status changes away from `active` in the dashboard drops out of the
  list within 15 minutes, or immediately when the pane is opened.
- Nothing the mod does in the background opens a permission dialog.

### Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Recall | Fresh session seeded with a brief. Not `claude --resume`. |
| Granularity | One project = one **company engagement**, its products nested under it. |
| DB access | Supabase MCP (`mcp__supabase__execute_sql`), narrow read, cached. No key in the mod. |
| Freshness | Session start + pane open + every 15 min. |
| Launch | New Windows Terminal tab. Copy-command fallback. |
| Brief upkeep | One nudge per company per session + `/lca-save` skill. |
| Approach | Thin mod (mechanics) + skill (brief writing, which is model work). |

### Scope of the list

The list shows companies Kevin has **worked on in Claude** (detected in a session or the
backfill) **and** whose `companies.status = 'active'`. It does not list every Active
company on the platform (81 today, across all users). This also sidesteps ownership
scoping: the live MCP identity is a super_admin account, not kevin@.

### Out of scope

- Products of a company never touched in Claude (the platform dashboard lists them).
- A "hide this company" action. Add if dev-session noise shows up in practice.
- Pruning old session files.
- Collapsible rows in the pane.
- The desktop app's launch path beyond the copy-command fallback.

## 2. Components

| Unit | Location | Does |
|---|---|---|
| `lca-projects` mod | `~/.claude/mods/lca-projects/` | detect, session files, status refresh, band, `/lca` pane, launch, nudge, `lca_context` tool |
| `lca-save` skill | `claude-skills-main/lca-save/SKILL.md`, junctioned into `~/.claude/skills/lca-save` | writes the brief |
| backfill script | `~/.claude/mods/lca-projects/backfill.ts` | one-time scan of past transcripts |
| data | `~/.claude/lca-projects/` | session files, status cache, briefs |

The mod sits next to `cache-timer` and `orchestrate-status` and follows
`orchestrate-status/hooks/register.tsx` for structure: `atom`/`read`/`update` state,
`$.command.register` in `session.start`, `$.ui.open` for the pane, a `PromptHint` render
hook for the band, and try/catch around every hook body with `$.ui.log(..., { to: 'debug' })`.

Pure logic (detection, parsing, list building, slug, sanitize, seed text) lives in one
module, `hooks/core.ts`, imported by both `register.tsx` and `backfill.ts`, so the
backfill and live detection cannot drift apart.

## 3. Storage

All under `~/.claude/lca-projects/`, independent of the working folder.

```
lca-projects/
  sessions/<session-id>.json   # written only by its own session: no write races
  status.json                  # DB cache; last writer wins (DB truth, harmless)
  <company-slug>/brief.md      # written by /lca-save, read by seeded sessions
```

### `sessions/<session-id>.json`

```json
{
  "cwd": "C:/Users/kmorg/climatepoint-eco-report-builder2",
  "products":  { "5e1f0c2a-7b3d-4c8e-9a1f-2b3c4d5e6f70": { "firstSeen": "2026-10-05T09:12:00Z", "lastSeen": "2026-10-06T23:30:00Z" } },
  "companies": { "<company-uuid>":                        { "firstSeen": "...", "lastSeen": "..." } },
  "pinned":    [ "<company-uuid>" ]
}
```

- `companies` holds IDs detected directly (for example a `company_id` input with no product).
- `pinned` holds companies tagged by `/lca-save <company>` when detection missed the session.
- A new ID is written immediately. `lastSeen` is rewritten at most once a minute.

### `status.json`

```json
{
  "checkedAt": "2026-10-07T08:00:00Z",
  "companies": { "<company-uuid>": { "name": "Acme Motors", "status": "active", "slug": "acme-motors" } },
  "products":  { "<product-uuid>": { "companyId": "<company-uuid>", "name": "ADP", "isArchived": false } },
  "followups": {
    "<company-uuid>": {
      "session":  { "kind": "followup", "status": "submitted", "submittedAt": "...", "expiresAt": "..." },
      "requests": [ { "reference": "...", "productId": "<product-uuid>", "status": "open", "sentAt": "...", "answered": 12, "total": 19 } ]
    }
  }
}
```

### Project list (computed on read, never stored)

1. Union every `sessions/*.json`: products, companies, pinned, each with its sessions,
   first/last seen and cwd.
2. Map each product to its company through `status.json`. Products not yet in
   `status.json` count as unknown and trigger a refresh (section 5).
3. Drop products with `isArchived = true`.
4. Keep companies with `status = 'active'`. A company appears if it has at least one
   live touched product, or it was detected/pinned directly.
5. Sort companies by most recent `lastSeen`. A company's launch folder is the `cwd` of
   its most recent session.

### Slug

The company name in kebab case (`Acme Motors` → `acme-motors`). The first
company to claim a slug keeps it. A later company whose name collides gets
`-<first 6 chars of company id>` appended. The slug is stored in `status.json` once
assigned and never recomputed, so a brief folder never moves: every refresh reads the
existing `status.json` first and carries each company's `slug` forward, assigning one
only to companies that have none. A company renamed on the platform keeps its slug.

### `brief.md`

```markdown
---
company_id: <uuid>
company: Acme Motors
products: [{id: 5e1f0c2a-7b3d-4c8e-9a1f-2b3c4d5e6f70, name: ADP}, {id: 9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d, name: BDP}]
updated: 2026-10-07
sessions: [1937fdab-ce11-43e7-a778-a95ab6345136]
---
## Where it stands      (one paragraph)
## Products             (per product: id, name, goal/standard, current state)
## Decisions            (what, who decided, date)
## Open gates           (blocking items, owner)
## Next actions         (concrete, with absolute file paths)
## Key files            (absolute paths: emails, research, round.json, report.md)
## Rules                (e.g. "no round commit without fresh export, preview and Kevin's yes")
```

The brief records decisions about follow-up rounds. It does not copy platform follow-up
state, which comes live from the database. Briefs hold client-confidential content and
stay under `~/.claude`, never in a repository.

## 4. Detection and nudge

### Detection

A `tool.call` hook that only observes: it calls `next(e)` and returns its result
unchanged, and never blocks or edits a call. It runs for the main loop and for subagents
(a `bom-weight-panel` worker touching a product counts).

| Tool name | Extracted |
|---|---|
| starts with `mcp__claude_ai_ClimatePoint__` or `mcp__climatepoint__` | top-level `product_id` and `company_id` in the input |
| `mcp__supabase__execute_sql` | every `product_id\s*=\s*'<uuid>'` and `company_id\s*=\s*'<uuid>'` in `query` (case-insensitive) |

Every extracted value must match
`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` (lowercased) or it is
dropped. This is the gate that keeps arbitrary tool input out of the status SQL.

Known miss: a session that only reads client files without calling a tool.
`/lca-save <company>` pins those.

Known noise: a dev session that touches a customer product while debugging gets tagged.
This is harmless because the list filters to Active, demo/prospect companies drop out,
and a brief changes only through `/lca-save`.

### Nudge

Once per company per session, on the main loop only (`e.agentId === undefined`), the
first time that company resolves to `active`, via
`$.session.append({ message: { type: 'user', content: [note] } })`:

```
<lca-project company="Acme Motors">
This session is working on LCA project Acme Motors.
Brief: C:\Users\kmorg\.claude\lca-projects\acme-motors\brief.md. Read it if you haven't.
Update it when decisions, gates or next actions change, or run /lca-save before closing.
</lca-project>
```

- With no brief yet, the last line reads: "No brief yet. Run /lca-save once there's
  something worth keeping."
- Skipped when the session's first user message already contains that brief path (a
  seeded session).
- The company name is sanitized first (section 5, Security).
- About 70 tokens, once.

## 5. Status refresh

### Triggers

- `session.start`
- `/lca` pane open
- every 15 minutes (`$.clock.every(900_000, …)`)
- an unknown product detected, debounced to 30 s

### Gating

1. If `mcp__supabase__execute_sql` is not in `$.tool.list()`, skip.
2. Background triggers (start, timer, unknown product) run the query only when
   `$.tool.check(...)` returns `allow`. `tool.check` opens no dialog and does not ask the
   classifier.
3. Pane open calls `$.tool.call` directly. A permission dialog may appear there, because
   the user is looking.
4. On skip, denial, error or parse failure: keep the existing `status.json`, do not
   retry until the next trigger.

`$.tool.call` from a plugin runs through the permission check and its dialog (engine
types, `tool.call` doc). Allowlisting `mcp__supabase__execute_sql` globally is rejected:
it would let the model run writes without a prompt.

### Spike (first plan task)

Determine:

1. Can the mod's own `tool.check` hook return `allow` for a call whose origin is this
   plugin and whose `query` matches the status-query template exactly, and does that
   decision hold at `tool.call` time?
2. In auto mode, does the classifier still evaluate a plugin-initiated `tool.call`
   after a hook allowed it?

If self-approval works, background refresh works everywhere. If it does not, background
refresh only runs where an allow already exists, the 15-minute timer is in effect
"start + pane open", and the plan reports this to Kevin instead of working around it.

### Query

One `execute_sql` call per refresh. Inputs are the validated product and company UUID
sets, interpolated as a literal `'{…}'::uuid[]` array. Shape (the plan finalizes exact
SQL against the live schema):

```sql
with p as (
  select id, company_id, name, is_archived
  from products
  where id = any('{<product uuids>}'::uuid[])
), c as (
  select id, name, status
  from companies
  where id in (select company_id from p) or id = any('{<company uuids>}'::uuid[])
), s as (   -- latest onboarding/follow-up session per company
  select distinct on (company_id) company_id, id, kind, status, submitted_at, expires_at
  from onboarding_sessions
  where company_id in (select id from c) and archived_at is null
  order by company_id, created_at desc
), r as (   -- non-archived requests with item counts
  select q.session_id, q.product_id, q.reference, q.status, q.sent_at,
         count(i.*) filter (where i.status = 'answered') answered, count(i.*) total
  from onboarding_requests q
  left join onboarding_request_items i on i.request_id = q.id
  where q.archived_at is null
    and q.session_id in (select id from onboarding_sessions where company_id in (select id from c))
  group by q.id
)
select json_build_object('companies', …, 'products', …, 'followups', …) as status;
```

The plan confirms: how `onboarding_requests.reference` relates to the CUST-0xx item IDs
(possibly `onboarding_request_items.req_ids`), and whether per-product item counts should
come from `onboarding_request_item_products` for `needs_per_product` items.

### Parsing

`execute_sql` returns text that wraps the rows in `<untrusted-data-…>` boundaries. Extract
the JSON array with
`/<untrusted-data-[^>]+>\s*(\[[\s\S]*\])\s*<\/untrusted-data-/`, parse it, read the single
`status` column. A test fixture captured from a real response fences the format.

### Security

- UUID validation before interpolation (section 4).
- Company and product names are user-controlled, and the nudge puts them into the model's
  context. Before use in the nudge or `lca_context`, strip `<`, `>` and control
  characters and cut to 80 characters.
- The mod never holds a Supabase key.

### Staleness

When `checkedAt` is older than 30 minutes, the band and pane show `status as of <age>`.

## 6. UI and launch

### Band

One line under the prompt hint, drawn by a `ui.render` hook on `PromptHint` that keeps
the engine's hint line (as `orchestrate-status` and `cache-timer` do):

```
LCA · 2 active · ⚑1
```

Hidden when the list is empty. `⚑n` counts companies with something waiting on Kevin:

- the latest follow-up session is `submitted` (client answered, review pending),
- a sent session expires within 3 days,
- a request is `draft` (not sent).

### `/lca` pane

`$.command.register({ name: 'lca', immediate: true, … })` opens `$.ui.open({ id: 'lca', title: 'LCA Projects' })`.
The pane render hook draws every company with its products nested, then a `Select` of
the companies:

```
LCA Projects                                  status as of 2m ago
Acme Motors          2 products · 3 sessions · 2h ago
    ADP   5e1f0c2a   last 2h ago (2 sessions)   follow-up R3: draft (not sent)
    BDP   9d8c7b6a   last 1d ago (1 session)    follow-up R2: open · 12/19 answered
    ⚑ client submitted follow-up 6 Oct, review pending
Acme Pumps               1 product · 1 session · 4d ago
    ...
> Start session: [Acme Motors] [Acme Pumps]
```

### Launch

Picking a company runs `$.process.spawn` with `wt.exe`, reusing the argument pattern the
`orchestrate` skill already uses to open tabs:

```
wt -w 0 new-tab --title "LCA · Acme Motors" -d <launch folder> claude "<seed>"
```

Seed:

> Resume LCA project Acme Motors. Read C:\Users\kmorg\.claude\lca-projects\acme-motors\brief.md first. Then check live follow-up state for its products with climatepoint_followup_guide, and continue from Next actions.

- The seed contains no `;` (wt's command separator) and no quote characters. A test
  enforces this, and company names are sanitized of both before they go into it.
- If the launch folder no longer exists, use the home folder.
- If `$.process` is unavailable (desktop app) or `wt.exe` fails to start, the pane shows
  the `claude "<seed>"` command to copy.
- With no brief yet, the seed reads: "Start LCA project Acme Motors. No brief yet.
  Call lca_context, review live follow-up state with climatepoint_followup_guide, and
  run /lca-save once there's something worth keeping."

## 7. `lca_context` tool and `/lca-save`

### `lca_context`

Registered with `$.tool.register({ name: 'lca_context', … })` at `session.start`, served
by a `tool.call` hook on `mcp__lca-projects__lca_context`. Input: optional `company`
(name, slug or UUID). Returns JSON:

```json
{
  "sessionId": "<this session>",
  "companies": [
    { "id": "...", "name": "Acme Motors", "slug": "acme-motors", "status": "active",
      "briefPath": "C:\\Users\\kmorg\\.claude\\lca-projects\\acme-motors\\brief.md",
      "briefExists": true,
      "products": [ { "id": "...", "name": "ADP", "lastSeen": "..." } ],
      "followups": { "session": { … }, "requests": [ … ] },
      "statusCheckedAt": "..." }
  ]
}
```

With `company` given and not yet tagged to this session, the tool resolves it against
`status.json` and adds it to this session's `pinned`. A name it cannot resolve returns an
error listing the known companies.

### `/lca-save [company]`

`claude-skills-main/lca-save/SKILL.md`:

1. Call `lca_context` (with `company` if given). More than one company and none named:
   ask which one.
2. Read the current brief if `briefExists`.
3. Rewrite it from the conversation: keep what is still true, move changed decisions into
   Decisions with date and who decided, update `## Products` per product, refresh the
   frontmatter (`products` names from `lca_context`, append `sessionId` to `sessions`,
   set `updated`).
4. Write the file and report a 5-line summary of what changed.

The skill never writes platform follow-up state into the brief, and never writes the
brief anywhere but `briefPath`.

## 8. Bootstrap and backfill

### Acme Motors

1. Run backfill (below) so session `1937fdab` with ADP `5e1f0c2a` and BDP `9d8c7b6a` is
   registered and `status.json` resolves the company.
2. Write `acme-motors/brief.md` from `client-lca-state.md`, mapped into the brief
   template.
3. Replace the body of `client-lca-state.md` with a one-line pointer to the brief, and
   update its `MEMORY.md` index line, so there is one source of truth.

### Backfill script

`backfill.ts` scans `~/.claude/projects/*/*.jsonl`, streaming line by line (transcripts
reach 15 MB). For each `tool_use` block it applies the same `core.ts` detection, and it
writes `sessions/<session-id>.json` with `cwd` from the transcript and first/last seen
from message timestamps. It skips session files that already exist, so it is safe to
re-run. It does not query the database. The next refresh resolves companies.

## 9. Errors

| Failure | Behaviour |
|---|---|
| Any hook throws | caught, logged to debug, engine unaffected |
| Supabase MCP absent | refresh skipped, last `status.json` shown as stale |
| `tool.check` not `allow` (background) | refresh skipped silently |
| Permission denied (pane open) | last `status.json` kept, pane notes the denial |
| Response unparsable | last `status.json` kept, debug log |
| `status.json` / session file corrupt | treated as empty and rewritten on next write |
| `wt.exe` or `$.process` unavailable | pane shows the copy command |
| Launch folder gone | home folder |

## 10. Testing

`lca-projects.test.ts`, same shape as `orchestrate-status.test.ts`, against `core.ts`:

- detection: ClimatePoint inputs and `execute_sql` queries taken from the `1937fdab`
  transcript; non-UUID values rejected; uppercase UUIDs lowercased.
- parsing: a captured `execute_sql` response; a malformed one returns null.
- project list: union across session files, Active filter, archived products dropped,
  directly-detected and pinned companies kept, sort by last seen, launch folder.
- slug: kebab case, collision suffix, stability.
- sanitize: `<`, `>`, control characters, 80-char cut.
- seed: no `;` or quotes for adversarial company names.
- flag count: submitted, expiring within 3 days, draft.

Backfill: one test over a small fixture transcript.

Manual smoke test:

1. In a fresh session, call a ClimatePoint tool on an Active company's product. The band
   pill appears.
2. The nudge fires once and not again for the same company.
3. `/lca` shows the company, its products and follow-up state.
4. Picking it opens a new tab with a seeded session that reads the brief.
5. `/lca-save` writes the brief. A second run updates it in place.
6. Change the company's status to `archived` in the dashboard. It drops out on the next
   pane open.
