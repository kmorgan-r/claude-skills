// Pure logic, shared by register.tsx (live sessions) and backfill.ts (past transcripts), so the
// two can never detect differently. No engine calls here: everything takes plain values.

import type { ProductRow, Project, Req } from '../types'
export type { ProductRow, Project, Req }

export const SQL_TOOL = 'mcp__supabase__execute_sql'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const DAY = 86_400_000

export type Ids = { products: string[]; companies: string[] }
export type Seen = { firstSeen: string; lastSeen: string }
// sessions/<session-id>.json. `companies` holds ids detected directly or pinned by lca_context.
export type SessionFile = { cwd: string; products: Record<string, Seen>; companies: Record<string, Seen> }

export type Onboarding = { status: string; submittedAt: string | null; expiresAt: string | null }
export type Followup = { onboarding: Onboarding | null; requests: Req[] }
// What the status query returns.
export type Fresh = {
  companies: Record<string, { name: string; status: string }>
  products: Record<string, { companyId: string; name: string; isArchived: boolean }>
  followups: Record<string, Followup>
}
// status.json: the query's answer plus when, and each company's slug.
export type Status = {
  checkedAt: string
  companies: Record<string, { name: string; status: string; slug: string }>
  products: Fresh['products']
  followups: Record<string, Followup>
}

export const uuid = (v: unknown): string | undefined => {
  if (typeof v !== 'string') return undefined
  const s = v.trim().toLowerCase()
  return UUID.test(s) ? s : undefined
}

const uniq = (xs: (string | undefined)[]) => [...new Set(xs.filter((x): x is string => x !== undefined))]
const has = (ids: Ids) => ids.products.length > 0 || ids.companies.length > 0
export const noIds = (): Ids => ({ products: [], companies: [] })

// A ClimatePoint MCP server is any whose name holds "climatepoint": claude_ai_ClimatePoint,
// climatepoint, climatepoint-remote-mcp.
export const isClimatePoint = (tool: string) => tool.startsWith('mcp__') && /climatepoint/i.test(tool.split('__')[1] ?? '')

// The product and company ids a tool call names. Every value must be a UUID: this is the gate
// that keeps arbitrary tool input out of the status SQL.
export function detect(tool: string, input: unknown): Ids {
  const o = (input !== null && typeof input === 'object' ? input : {}) as Record<string, unknown>
  if (isClimatePoint(tool)) return { products: uniq([uuid(o.product_id)]), companies: uniq([uuid(o.company_id)]) }
  if (tool !== SQL_TOOL || typeof o.query !== 'string') return noIds()
  const found = noIds()
  for (const m of o.query.matchAll(/\b(product_id|company_id)\s*=\s*'([^']*)'/gi)) {
    const id = uuid(m[2])
    if (id) (m[1]!.toLowerCase() === 'product_id' ? found.products : found.companies).push(id)
  }
  return { products: uniq(found.products), companies: uniq(found.companies) }
}

export const emptySession = (cwd: string): SessionFile => ({ cwd, products: {}, companies: {} })

// Records ids seen at `now` (ISO): a new id at once, a known id's lastSeen at most once a minute.
// Mutates `file`; true when it changed and must be written.
export function touch(file: SessionFile, ids: Ids, now: string): boolean {
  let changed = false
  const mark = (map: Record<string, Seen>, id: string) => {
    const seen = map[id]
    if (!seen) {
      map[id] = { firstSeen: now, lastSeen: now }
      changed = true
    } else if (Date.parse(now) - Date.parse(seen.lastSeen) >= 60_000) {
      seen.lastSeen = now
      changed = true
    }
  }
  for (const id of ids.products) mark(file.products, id)
  for (const id of ids.companies) mark(file.companies, id)
  return changed
}

const isMap = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)

// A corrupt or half-written file reads as null: treated as empty, rewritten on the next write.
export function parseSession(text: string): SessionFile | null {
  try {
    const o: unknown = JSON.parse(text)
    if (!isMap(o) || !isMap(o.products) || !isMap(o.companies)) return null
    return { cwd: typeof o.cwd === 'string' ? o.cwd : '', products: o.products as SessionFile['products'], companies: o.companies as SessionFile['companies'] }
  } catch {
    return null
  }
}

export function parseStatusFile(text: string): Status | null {
  try {
    const o: unknown = JSON.parse(text)
    if (!isMap(o) || typeof o.checkedAt !== 'string' || !isMap(o.companies) || !isMap(o.products) || !isMap(o.followups)) return null
    return o as Status
  } catch {
    return null
  }
}

// Every id any session touched: what the status query asks about.
export function allIds(sessions: Record<string, SessionFile>): Ids {
  const ids = noIds()
  for (const s of Object.values(sessions)) {
    ids.products.push(...Object.keys(s.products))
    ids.companies.push(...Object.keys(s.companies))
  }
  return { products: uniq(ids.products.map(uuid)), companies: uniq(ids.companies.map(uuid)) }
}

// Ids neither status.json nor the last query this process ran knows: a refresh is due.
// `queried` keeps a product the database does not return from asking again and again.
export const unknownIds = (ids: Ids, status: Status | null, queried: Set<string>) => [
  ...ids.products.filter(id => !status?.products[id] && !queried.has(id)),
  ...ids.companies.filter(id => !status?.companies[id] && !queried.has(id)),
]

const arr = (xs: string[]) => `'{${uniq(xs.map(uuid)).join(',')}}'::uuid[]`

// One read: the products and companies asked about, each company's latest live onboarding session,
// and its live follow-up requests with their link's state and item counts. `round` numbers a
// product's requests in creation order, archived ones included, as the platform's rounds run.
export function statusQuery(ids: Ids): string {
  return `with p as (
  select id, company_id, name, is_archived from products
  where id = any(${arr(ids.products)})
), c as (
  select id, name, status from companies
  where id in (select company_id from p) or id = any(${arr(ids.companies)})
), o as (
  select distinct on (company_id) company_id, status, submitted_at, expires_at from onboarding_sessions
  where company_id in (select id from c) and kind = 'onboarding' and archived_at is null
  order by company_id, created_at desc
), r as (
  select s.company_id, q.product_id, q.status, q.sent_at, q.archived_at,
         s.status session_status, s.submitted_at, s.expires_at,
         row_number() over (partition by q.product_id order by q.created_at) round,
         (select count(*) from onboarding_request_items i where i.request_id = q.id and i.status = 'answered') answered,
         (select count(*) from onboarding_request_items i where i.request_id = q.id) total
  from onboarding_requests q join onboarding_sessions s on s.id = q.session_id
  where s.company_id in (select id from c)
)
select json_build_object(
  'companies', coalesce((select json_object_agg(id, json_build_object('name', name, 'status', status)) from c), '{}'),
  'products', coalesce((select json_object_agg(id, json_build_object('companyId', company_id, 'name', name, 'isArchived', is_archived)) from p), '{}'),
  'followups', coalesce((select json_object_agg(c.id, json_build_object(
    'onboarding', (select json_build_object('status', o.status, 'submittedAt', o.submitted_at, 'expiresAt', o.expires_at) from o where o.company_id = c.id),
    'requests', coalesce((select json_agg(json_build_object('round', r.round, 'productId', r.product_id, 'status', r.status, 'sentAt', r.sent_at, 'answered', r.answered, 'total', r.total, 'sessionStatus', r.session_status, 'submittedAt', r.submitted_at, 'expiresAt', r.expires_at) order by r.product_id, r.round) from r where r.company_id = c.id and r.archived_at is null), '[]')
  )) from c), '{}')
) as status`
}

// execute_sql's text: the rows as a JSON array between <untrusted-data-…> boundaries, either bare
// or inside a {"result": "..."} envelope. Null for anything else.
export function parseStatus(text: string): Fresh | null {
  let body = text
  try {
    const o: unknown = JSON.parse(text)
    if (isMap(o) && typeof o.result === 'string') body = o.result
  } catch {
    // not the envelope: the bare form
  }
  const m = /<untrusted-data-[^>]+>\s*(\[[\s\S]*\])\s*<\/untrusted-data-/.exec(body)
  if (!m) return null
  try {
    const rows: unknown = JSON.parse(m[1]!)
    const s = Array.isArray(rows) && isMap(rows[0]) ? rows[0].status : undefined
    return isMap(s) && isMap(s.companies) && isMap(s.products) && isMap(s.followups) ? (s as Fresh) : null
  } catch {
    return null
  }
}

// Ø, Æ, ß, Đ and Ł have no NFKD decomposition: spelled out first, or a Nordic name loses letters.
const FOLD: Record<string, string> = { ø: 'o', æ: 'ae', ß: 'ss', đ: 'd', ł: 'l' }

export const kebab = (name: string) =>
  name
    .toLowerCase()
    .replace(/[øæßđł]/g, c => FOLD[c] ?? c)
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'company'

// A company keeps the slug it was first given (its brief folder never moves, a rename included);
// a newcomer whose name collides gets -<first 6 chars of its id>. A company missing from an answer
// (the query asks for every id ever touched, so: deleted) stays as `gone`, keeping its slug.
export function mergeStatus(prev: Status | null, fresh: Fresh, now: string): Status {
  const taken = new Set(Object.values(prev?.companies ?? {}).map(c => c.slug))
  const companies: Status['companies'] = Object.fromEntries(Object.entries(prev?.companies ?? {}).map(([id, c]) => [id, { ...c, status: 'gone' }]))
  for (const [id, c] of Object.entries(fresh.companies)) {
    let slug = prev?.companies[id]?.slug
    if (!slug) {
      slug = kebab(c.name)
      if (taken.has(slug)) slug = `${slug}-${id.slice(0, 6)}`
      taken.add(slug)
    }
    companies[id] = { name: c.name, status: c.status, slug }
  }
  return { checkedAt: now, companies, products: fresh.products, followups: fresh.followups }
}

// Names are user-controlled and reach the model (nudge, lca_context) and a .cmd file (launch):
// no markup, quotes, cmd/wt metacharacters or control characters, at most 80 characters.
export const sanitize = (s: string) =>
  s
    .replace(/\p{Cc}/gu, ' ')
    .replace(/[<>"'`;%^&|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)

export const briefPath = (root: string, slug: string) => `${root.replace(/\//g, '\\')}\\${slug}\\brief.md`

const date = (iso: string | null) => (iso ? new Date(iso).toISOString().slice(0, 10) : '?')

// What waits on Kevin: a draft not sent, a link the client submitted that is not closed yet, a
// link that expires within 3 days; the same for the onboarding session.
export function flags(f: Followup | undefined, nowMs: number, productName: (id: string) => string): string[] {
  if (!f) return []
  const soon = (iso: string | null) => {
    const t = iso ? Date.parse(iso) : NaN
    return t > nowMs && t - nowMs <= 3 * DAY
  }
  const out: string[] = []
  for (const r of f.requests) {
    const who = `${r.productId ? productName(r.productId) : 'company'} R${r.round}`
    if (r.status === 'draft') out.push(`${who}: draft, not sent`)
    else if (r.status === 'open' && r.sessionStatus === 'submitted') out.push(`${who}: client submitted ${date(r.submittedAt)}, review pending`)
    else if (r.status === 'open' && r.sessionStatus === 'sent' && soon(r.expiresAt)) out.push(`${who}: link expires ${date(r.expiresAt)}`)
  }
  const o = f.onboarding
  if (o?.status === 'submitted') out.push(`onboarding submitted ${date(o.submittedAt)}, review pending`)
  else if (o?.status === 'sent' && soon(o.expiresAt)) out.push(`onboarding link expires ${date(o.expiresAt)}`)
  return out
}

export const requestLine = (r: Req) =>
  r.status === 'draft' ? `R${r.round}: draft (not sent)` : `R${r.round}: ${r.status} · ${r.answered}/${r.total} answered · link ${r.sessionStatus}`

const latest = (f: Followup | undefined, productId: string) =>
  f?.requests.filter(r => r.productId === productId).sort((a, b) => b.round - a.round)[0]

// The list: every session's ids, products mapped to companies through status.json, archived
// products dropped, Active companies kept (with a live touched product, or detected directly),
// newest first. briefExists stays false and cwd the latest session's until register.tsx reads
// the brief.
export function buildProjects(sessions: Record<string, SessionFile>, status: Status | null, nowMs: number, root: string): Project[] {
  if (!status) return []
  type Acc = { lastSeen: string; sessions: Set<string>; cwd: string; cwdAt: string; products: Map<string, { lastSeen: string; sessions: Set<string> }> }
  const acc = new Map<string, Acc>()
  const at = (cid: string, sid: string, file: SessionFile, seen: string) => {
    let a = acc.get(cid)
    if (!a) acc.set(cid, (a = { lastSeen: '', sessions: new Set(), cwd: '', cwdAt: '', products: new Map() }))
    a.sessions.add(sid)
    if (seen > a.lastSeen) a.lastSeen = seen
    if (file.cwd && seen > a.cwdAt) {
      a.cwd = file.cwd
      a.cwdAt = seen
    }
    return a
  }
  for (const [sid, file] of Object.entries(sessions)) {
    for (const [pid, seen] of Object.entries(file.products)) {
      const p = status.products[pid]
      if (!p || p.isArchived) continue
      const a = at(p.companyId, sid, file, seen.lastSeen)
      const row = a.products.get(pid) ?? { lastSeen: '', sessions: new Set<string>() }
      row.sessions.add(sid)
      if (seen.lastSeen > row.lastSeen) row.lastSeen = seen.lastSeen
      a.products.set(pid, row)
    }
    for (const [cid, seen] of Object.entries(file.companies)) at(cid, sid, file, seen.lastSeen)
  }
  const out: Project[] = []
  for (const [cid, a] of acc) {
    const c = status.companies[cid]
    if (c?.status !== 'active') continue
    const f = status.followups[cid]
    const name = (id: string) => sanitize(status.products[id]?.name ?? id.slice(0, 8))
    const products = [...a.products]
      .map(([id, r]) => ({ id, name: name(id), lastSeen: r.lastSeen, sessions: r.sessions.size, latest: latest(f, id) }))
      .sort((x, y) => y.lastSeen.localeCompare(x.lastSeen))
    out.push({
      id: cid,
      name: sanitize(c.name),
      slug: c.slug,
      lastSeen: a.lastSeen,
      sessions: a.sessions.size,
      cwd: a.cwd,
      briefPath: briefPath(root, c.slug),
      briefExists: false,
      products,
      flags: flags(f, nowMs, name),
    })
  }
  return out.sort((x, y) => y.lastSeen.localeCompare(x.lastSeen))
}

// What lca_context returns for one session: its companies whatever their status.
export function contextOf(file: SessionFile, status: Status | null, root: string) {
  if (!status) return []
  const byCompany = new Map<string, { id: string; name: string; lastSeen: string }[]>()
  for (const [pid, seen] of Object.entries(file.products)) {
    const p = status.products[pid]
    if (!p) continue
    byCompany.set(p.companyId, [...(byCompany.get(p.companyId) ?? []), { id: pid, name: sanitize(p.name), lastSeen: seen.lastSeen }])
  }
  for (const cid of Object.keys(file.companies)) if (!byCompany.has(cid)) byCompany.set(cid, [])
  return [...byCompany]
    .filter(([cid]) => status.companies[cid])
    .map(([cid, products]) => {
      const c = status.companies[cid]!
      return {
        id: cid,
        name: sanitize(c.name),
        slug: c.slug,
        status: c.status,
        briefPath: briefPath(root, c.slug),
        briefExists: false,
        products,
        followups: status.followups[cid] ?? null,
        statusCheckedAt: status.checkedAt,
      }
    })
}

// A company named by UUID (known or not yet; a known product's names its company), slug or name,
// case-insensitive; a live one before a gone one.
export function resolveCompany(q: string, status: Status | null): string | undefined {
  const id = uuid(q)
  if (id) return status?.products[id]?.companyId ?? id
  const k = q.trim().toLowerCase()
  const hits = Object.entries(status?.companies ?? {}).filter(([, c]) => c.slug === k || c.name.toLowerCase() === k)
  return (hits.find(([, c]) => c.status !== 'gone') ?? hits[0])?.[0]
}

// The brief's launch_cwd frontmatter field, when set. The model writes the brief, and wt reads `;`
// as its command separator: a value holding one, or a quote, is ignored.
export function launchCwd(brief: string): string | undefined {
  const front = /^---\r?\n([\s\S]*?)\r?\n---/.exec(brief)?.[1]
  const v = front ? /^launch_cwd:\s*(.+?)\s*$/m.exec(front)?.[1] : undefined
  return v && !/[;"]/.test(v) ? v : undefined
}

export const ago = (ms: number) =>
  ms < 60_000 ? 'just now' : ms < 3_600_000 ? `${Math.floor(ms / 60_000)}m ago` : ms < DAY ? `${Math.floor(ms / 3_600_000)}h ago` : `${Math.floor(ms / DAY)}d ago`
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const since = (iso: string, nowMs: number) => (iso ? ago(nowMs - Date.parse(iso)) : 'never')

// The band: null hides it.
export function band(projects: Project[], checkedAt: string | null, nowMs: number): string | null {
  if (projects.length === 0) return null
  const flagged = projects.filter(p => p.flags.length > 0).length
  const stale = checkedAt && nowMs - Date.parse(checkedAt) > 30 * 60_000 ? `status as of ${ago(nowMs - Date.parse(checkedAt))}` : ''
  return [`LCA · ${projects.length} active`, flagged ? `⚑${flagged}` : '', stale].filter(Boolean).join(' · ')
}

export type PaneRow = { kind: 'head' | 'company' | 'product' | 'flag' | 'empty'; text: string }

export function paneRows(projects: Project[], checkedAt: string | null, nowMs: number): PaneRow[] {
  const rows: PaneRow[] = [{ kind: 'head', text: checkedAt ? `status as of ${since(checkedAt, nowMs)}` : 'status not checked yet' }]
  if (projects.length === 0) rows.push({ kind: 'empty', text: 'No active LCA projects yet. Call a ClimatePoint tool on a product of an Active company.' })
  for (const p of projects) {
    rows.push({ kind: 'company', text: `${p.name}  ${plural(p.products.length, 'product')} · ${plural(p.sessions, 'session')} · ${since(p.lastSeen, nowMs)}` })
    for (const r of p.products) {
      const follow = r.latest ? `   follow-up ${requestLine(r.latest)}` : ''
      rows.push({ kind: 'product', text: `    ${r.name.slice(0, 40)}   ${r.id.slice(0, 8)}   last ${since(r.lastSeen, nowMs)} (${plural(r.sessions, 'session')})${follow}` })
    }
    for (const f of p.flags) rows.push({ kind: 'flag', text: `    ⚑ ${f}` })
  }
  return rows
}

// The model's notice, once per company per session. `name` is sanitized already.
export const nudge = (name: string, brief: string, exists: boolean) =>
  [
    `<lca-project company="${name}">`,
    `This session is working on LCA project ${name}.`,
    exists ? `Brief: ${brief}. Read it if you haven't.` : `Brief: ${brief}.`,
    exists ? 'Update it when decisions, gates or next actions change, or run /lca-save before closing.' : "No brief yet. Run /lca-save once there's something worth keeping.",
    '</lca-project>',
  ].join('\n')

// The first prompt of a launched session. No ; (wt's separator), quotes or cmd metacharacters:
// `name` is sanitized and the fixed text holds none.
export const seed = (name: string, brief: string, exists: boolean) =>
  exists
    ? `Resume LCA project ${name}. Read ${brief} first. Then check live follow-up state for its products with climatepoint_followup_guide, and continue from Next actions.`
    : `Start LCA project ${name}. No brief yet. Call lca_context, review live follow-up state with climatepoint_followup_guide, and run /lca-save once there is something worth keeping.`

// orchestrate SKILL.md, step 4: a tab wt opens inherits this session's environment, and a claude
// started with these markers runs as a child session that never registers and saves no transcript.
const CHILD_MARKERS = [
  'CLAUDECODE',
  'CLAUDE_CODE_CHILD_SESSION',
  'CLAUDE_CODE_ENTRYPOINT',
  'CLAUDE_CODE_MESSAGING_SOCKET',
  'CLAUDE_CODE_MESSAGING_TOKEN',
  'CLAUDE_CODE_SESSION_ATTENDED',
  'CLAUDE_CODE_SESSION_ID',
  'CLAUDE_PID',
  'CLAUDE_EFFORT',
  'CLAUDE_CODE_EXECPATH',
  'CLAUDE_PLUGIN_DATA',
  'AI_AGENT',
]

// launch.cmd: carries the seed so nothing has to survive wt's argument splitting.
export const launcher = (claude: string, seedText: string) =>
  ['@echo off', ...CHILD_MARKERS.map(v => `set ${v}=`), `"${claude}" "${seedText}"`, 'exit /b 0', ''].join('\r\n')

export const wtArgs = (slug: string, folder: string, cmdPath: string) => [
  'wt.exe',
  '-w',
  '0',
  'new-tab',
  '--title',
  `lca-${slug}`,
  '-d',
  folder.replace(/\//g, '\\').replace(/\\+$/, ''),
  'cmd',
  '/c',
  cmdPath,
]

// backfill.ts, one transcript line at a time: the ids of every tool_use in an assistant line, seen
// at the line's timestamp, and the session's folder (the last one a tool call ran in).
export function scanLine(file: SessionFile, line: string): void {
  if (!line.includes('"tool_use"')) return // most lines, and every multi-MB attachment line
  let o: { type?: unknown; timestamp?: unknown; cwd?: unknown; message?: { content?: unknown } }
  try {
    o = JSON.parse(line)
  } catch {
    return
  }
  if (o.type !== 'assistant' || typeof o.timestamp !== 'string' || !Array.isArray(o.message?.content)) return
  const at = o.timestamp
  for (const b of o.message.content as { type?: unknown; name?: unknown; input?: unknown }[]) {
    if (b?.type !== 'tool_use' || typeof b.name !== 'string') continue
    const ids = detect(b.name, b.input)
    if (!has(ids)) continue
    if (typeof o.cwd === 'string' && o.cwd) file.cwd = o.cwd
    for (const [map, list] of [
      [file.products, ids.products],
      [file.companies, ids.companies],
    ] as const) {
      for (const id of list) {
        const s = map[id]
        if (!s) map[id] = { firstSeen: at, lastSeen: at }
        else {
          if (at < s.firstSeen) s.firstSeen = at
          if (at > s.lastSeen) s.lastSeen = at
        }
      }
    }
  }
}

export const hasIds = (file: SessionFile) => has({ products: Object.keys(file.products), companies: Object.keys(file.companies) })
