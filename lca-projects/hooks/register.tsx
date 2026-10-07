import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { LcaView } from '../types'
import {
  SQL_TOOL,
  allIds,
  band,
  buildProjects,
  contextOf,
  detect,
  emptySession,
  launchCwd,
  launcher,
  mergeStatus,
  nudge,
  paneRows,
  parseSession,
  parseStatus,
  parseStatusFile,
  resolveCompany,
  sanitize,
  seed,
  statusQuery,
  touch,
  unknownIds,
  wtArgs,
} from './core'
import type { Ids, Project, SessionFile, Status } from './core'

const PLUGIN = 'lca-projects'
const PANE = 'lca'
// A registered tool's name is outside the engine's tool-name union: matched by pattern.
const CONTEXT_TOOL = /^mcp__lca-projects__lca_context$/
const FRESH_MS = 15 * 60_000

const view = atom({ plugin: 'lca-projects', key: 'view' } as const, { projects: [], checkedAt: null, note: null } as LcaView)

// Module state: a reload starts it over, as orchestrate-status's does.
let root = '' // ~/.claude/lca-projects
let cwd = ''
let sid = ''
let mine: SessionFile = emptySession('')
const mainIds = new Set<string>() // ids the main loop touched: only their companies get the nudge
const nudged = new Set<string>()
const queried = new Set<string>()
let busy = false
let debounced = false
let interactive = false // a person at the prompt: a -p run or the SDK is never nudged

const log = ($: EngineInterface, err: unknown) => $.ui.log(`lca-projects: ${err}`, { to: 'debug' })
const iso = async ($: EngineInterface) => new Date(await $.clock.now()).toISOString()
const saveMine = ($: EngineInterface) => $.fs.write(`${root}\\sessions\\${sid}.json`, JSON.stringify(mine, null, 2))

async function loadStatus($: EngineInterface): Promise<Status | null> {
  try {
    return parseStatusFile(String(await $.fs.read(`${root}\\status.json`)))
  } catch {
    return null
  }
}

// A /clear or an in-process resume goes on under a new session id, and no session.start fires for
// it: whatever runs first under the new id begins this session's bookkeeping over. The file is read
// before any state moves, so a second caller arriving meanwhile finds the switch done or not begun.
async function ensureSession($: EngineInterface) {
  const id = await $.session.id()
  if (id === sid) return
  let file = emptySession(cwd)
  try {
    file = parseSession(String(await $.fs.read(`${root}\\sessions\\${id}.json`))) ?? file
  } catch {
    // a new session
  }
  if (id === sid) return
  sid = id
  mine = file
  mainIds.clear()
  nudged.clear()
}

// Every session's file; this session's from memory, as it is written.
async function loadSessions($: EngineInterface): Promise<Record<string, SessionFile>> {
  const out: Record<string, SessionFile> = {}
  try {
    for (const f of await $.fs.list(`${root}\\sessions`)) {
      if (!f.name.endsWith('.json') || f.name === `${sid}.json`) continue
      try {
        const s = parseSession(String(await $.fs.read(`${root}\\sessions\\${f.name}`)))
        if (s) out[f.name.slice(0, -5)] = s
      } catch {
        // gone between list and read
      }
    }
  } catch {
    // no sessions folder yet
  }
  out[sid] = mine
  return out
}

// The list, into the atom the render hooks read; then the nudge for any company newly resolved.
async function recompute($: EngineInterface) {
  await ensureSession($) // a background refresh after a /clear lands here first
  const [sessions, status, now] = await Promise.all([loadSessions($), loadStatus($), $.clock.now()])
  const projects = buildProjects(sessions, status, now, root)
  for (const p of projects) {
    try {
      const brief = String(await $.fs.read(p.briefPath))
      p.briefExists = true
      p.cwd = launchCwd(brief) ?? p.cwd
    } catch {
      // no brief yet
    }
  }
  await update($, view, v => ({ ...v, projects, checkedAt: status?.checkedAt ?? null }))
  await nudgeNew($, projects, status)
  return { projects, status }
}

// Once per company per session, on an interactive session's main loop only: nobody watches a
// headless run rewrite a brief. Skipped when the conversation already holds the brief's path (a
// seeded session) or this notice (a resume, a reload).
async function nudgeNew($: EngineInterface, projects: Project[], status: Status | null) {
  if (!interactive) return
  const companies = new Set([...mainIds].map(id => status?.products[id]?.companyId ?? id))
  const due = projects.filter(p => companies.has(p.id) && !nudged.has(p.id))
  if (due.length === 0) return
  const said = (await $.session.messages()).filter(m => m.role === 'user').map(m => m.text)
  for (const p of due) {
    nudged.add(p.id)
    const tag = `<lca-project company="${p.name}">`
    if (said.some(t => t.includes(p.briefPath) || t.includes(tag))) continue
    try {
      await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: nudge(p.name, p.briefPath, p.briefExists) }] } })
    } catch (err) {
      log($, err) // one company's notice failing leaves the others'
    }
  }
}

async function observe($: EngineInterface, tool: string, input: unknown, agentId: string | undefined) {
  const ids = detect(tool, input)
  const all = [...ids.products, ...ids.companies]
  if (all.length === 0) return
  await ensureSession($)
  const fresh = agentId === undefined && all.some(id => !mainIds.has(id))
  if (agentId === undefined) for (const id of all) mainIds.add(id)
  const changed = touch(mine, ids, await iso($))
  if (changed) await saveMine($)
  if (!changed && !fresh) return
  const { status } = await recompute($)
  if (unknownIds(ids, status, queried).length > 0) soon($)
}

// An unknown product refreshes 30 s later, once however many arrive meanwhile.
function soon($: EngineInterface) {
  if (debounced) return
  debounced = true
  $.clock.after(30_000, () => {
    debounced = false
    void refresh($, 'unknown')
  })
}

type Why = 'start' | 'timer' | 'unknown' | 'pane'

// One status query for every id any session touched. Background triggers run it only where a
// permission rule already allows it: a plugin's own tool.check hook never sees its own check, so
// it cannot approve itself. The pane asks with the user looking. Any failure keeps status.json.
async function refresh($: EngineInterface, why: Why) {
  if (busy) {
    if (why === 'unknown') soon($) // the running one may predate the new id
    return
  }
  busy = true
  try {
    const status = await loadStatus($)
    if (!status && (await $.fs.exists(`${root}\\status.json`))) {
      // Unreadable, not absent: rewriting it would give every company its slug afresh.
      log($, 'status.json is unreadable; refresh skipped until it is fixed or deleted')
      if (why === 'pane') await update($, view, v => ({ ...v, note: 'status.json is unreadable: fix or delete it to refresh.' }))
      return
    }
    const now = await $.clock.now()
    if ((why === 'start' || why === 'timer') && status && now - Date.parse(status.checkedAt) < FRESH_MS) return
    if (!(await $.tool.list()).some(t => t.name === SQL_TOOL)) {
      if (why === 'pane') await update($, view, v => ({ ...v, note: 'No Supabase MCP in this session: showing the last saved status.' }))
      return
    }
    const ids: Ids = allIds(await loadSessions($))
    if (ids.products.length === 0 && ids.companies.length === 0) return
    const query = statusQuery(ids)
    if (why !== 'pane' && (await $.tool.check({ tool: SQL_TOOL, input: { query } })).decision !== 'allow') return
    const r = await $.tool.call({ tool: SQL_TOOL, query })
    if (r.deny !== undefined) {
      if (why === 'pane') await update($, view, v => ({ ...v, note: `Status refresh denied: ${r.deny}` }))
      return
    }
    const fresh = r.isError ? null : parseStatus(String(r.text ?? ''))
    if (!fresh) {
      log($, `status ${r.isError ? 'query failed' : 'response unparsable'}: ${String(r.text ?? '').slice(0, 200)}`)
      if (why === 'pane') await update($, view, v => ({ ...v, note: 'Status refresh failed; showing the last saved status.' }))
      return
    }
    await $.fs.write(`${root}\\status.json`, JSON.stringify(mergeStatus(status, fresh, new Date(now).toISOString()), null, 2))
    for (const id of [...ids.products, ...ids.companies]) queried.add(id)
    await update($, view, v => ({ ...v, note: null }))
    await recompute($)
  } catch (err) {
    log($, err) // a call cancelled at its permission dialog rejects, and lands here
    if (why === 'pane') await update($, view, v => ({ ...v, note: 'Status refresh failed; showing the last saved status.' }))
  } finally {
    busy = false
  }
}

async function launch($: EngineInterface, companyId: string) {
  try {
    const p = (await read($, view)).projects.find(x => x.id === companyId)
    if (!p) return
    const home = (await $.env.get('USERPROFILE')) ?? 'C:\\'
    const folder = p.cwd && (await $.fs.exists(p.cwd)) ? p.cwd : home
    const cmd = `${root}\\${p.slug}\\launch.cmd`
    await $.fs.write(cmd, launcher((await $.env.get('CLAUDE_CODE_EXECPATH')) ?? 'claude', seed(p.name, p.briefPath, p.briefExists)))
    const r = await $.process.run(wtArgs(p.slug, folder, cmd), { timeoutMs: 15_000 }).catch(() => null)
    const note = r?.exitCode === 0 ? `Opened ${p.name} in a new tab.` : `Could not open a tab. Run: cmd /c "${cmd}"`
    await update($, view, v => ({ ...v, note }))
  } catch (err) {
    log($, err)
  }
}

const COLOR = { head: '#94a3b8', company: '#38bdf8', product: '#e2e8f0', flag: '#f59e0b', empty: '#94a3b8' } as const

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    // A start (a new process's resume included) begins this session's bookkeeping over.
    queried.clear()
    busy = false
    debounced = false
    cwd = e.cwd
    interactive = e.isInteractive
    sid = ''
    try {
      const config = (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${await $.env.get('USERPROFILE')}\\.claude`
      root = `${config.replace(/\//g, '\\')}\\lca-projects`
      await ensureSession($)
      await $.command.register({ name: 'lca', description: 'Active LCA client projects: products, follow-up state, start a seeded session', immediate: true })
      await $.tool.register({
        name: 'lca_context',
        description:
          "This session's LCA client companies: id, name, status, brief path (and whether it exists), the products touched and live follow-up state. With `company` (name, slug or UUID) not yet tagged, tags it to this session. /lca-save calls it first.",
        inputSchema: { type: 'object', properties: { company: { type: 'string', description: 'Company name, slug or UUID' } } },
      })
      void recompute($).catch(err => log($, err)) // reads every session file: the start never waits on it
      void refresh($, 'start')
      $.clock.every(FRESH_MS, () => {
        $.ui.invalidate('ui.render') // the band's "status as of" moves with time
        void refresh($, 'timer')
      })
    } catch (err) {
      log($, err)
    }
    return started
  })

  // Observe only: the call runs unchanged, whatever detection does.
  on('tool.call', async ($, e, next) => {
    try {
      await observe($, e.tool, e, e.agentId)
    } catch (err) {
      log($, err)
    }
    return next(e)
  }).catch(($, e, next) => next(e)) // overran: the call still runs

  on('tool.call', { tool: CONTEXT_TOOL }, async ($, e) => {
    try {
      await ensureSession($)
      const q = (e as { company?: unknown }).company
      let status = await loadStatus($)
      if (typeof q === 'string' && q.trim()) {
        const id = resolveCompany(q, status)
        if (!id) {
          const known = Object.values(status?.companies ?? {}).map(c => sanitize(c.name))
          return { deny: `Unknown company "${sanitize(q)}". Known: ${known.join(', ') || 'none yet'}.` }
        }
        nudged.add(id) // /lca-save is about to write the brief: no notice needed
        if (touch(mine, { products: [], companies: [id] }, await iso($))) await saveMine($)
        if (!status?.companies[id] || status.companies[id].status === 'gone') {
          await refresh($, 'unknown')
          status = await loadStatus($)
        }
        void recompute($).catch(err => log($, err))
      }
      const companies = contextOf(mine, status, root).filter(c => typeof q !== 'string' || !q.trim() || c.id === resolveCompany(q, status))
      for (const c of companies) c.briefExists = await $.fs.exists(c.briefPath)
      const note = companies.length === 0 ? 'No company resolved for this session yet. Open /lca to refresh status, or pass company.' : undefined
      return { result: JSON.stringify({ sessionId: sid, companies, note }, null, 2) }
    } catch (err) {
      log($, err)
      return { deny: `lca_context failed: ${err}` }
    }
  }).catch(() => ({ deny: 'lca_context timed out' }))

  on('command.run', { command: 'lca' }, async $ => {
    await $.ui.open({ id: PANE, title: 'LCA Projects', focus: true }) // keys go straight to the picker
    void recompute($).then(() => refresh($, 'pane')).catch(err => log($, err))
    return { text: 'LCA Projects pane opened.' }
  })

  on('ui.select', { plugin: PLUGIN, element: 'lca-launch' }, async ($, e, next) => {
    const picked = await next(e)
    void launch($, e.value)
    return picked
  })

  // Under the prompt, beneath the engine's hint line, which stays whole.
  on('ui.render', { component: 'PromptHint' }, async ($, e, next) => {
    const drawn = await next(e)
    const v = await read($, view)
    const text = band(v.projects, v.checkedAt, await $.clock.now())
    if (!text) return drawn
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        {drawn}
        <Text color={v.projects.some(p => p.flags.length > 0) ? COLOR.flag : COLOR.company}>{text}</Text>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const [v, now] = await Promise.all([read($, view), $.clock.now()])
    const els = $.ui.resolve(e)
    const { Box, Text } = els
    const Select = 'Select' in els ? els.Select : undefined // mobile has none, and no wt.exe either
    // Picker and note first: the pane has a fixed height and clips from the bottom, so a long
    // project list would push them out of view.
    return (
      <Box flexDirection="column" width={e.props.bodyColumns}>
        {Select && v.projects.length > 0 && (
          <Select key="lca-launch" autoFocus label="Start session: " options={v.projects.map(p => ({ value: p.id, label: p.name }))} onSelect={() => {}} />
        )}
        {v.note && <Text color={COLOR.flag}>{v.note}</Text>}
        {paneRows(v.projects, v.checkedAt, now).map((r, i) => (
          <Text key={`r${i}`} bold={r.kind === 'company'} color={COLOR[r.kind]} wrap="truncate">
            {r.text}
          </Text>
        ))}
      </Box>
    )
  })
}
