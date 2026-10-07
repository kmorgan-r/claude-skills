// One-time: registers past sessions' ClimatePoint products from their transcripts, with the same
// detection the mod runs live (hooks/core.ts). Never queries the database: the next status
// refresh resolves companies. Skips a session whose file exists, so it is safe to re-run.
//
//   node lca-projects/backfill.ts [root]      root defaults to ~/.claude/lca-projects
import { createReadStream, existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { createInterface } from 'node:readline'

import { emptySession, hasIds, scanLine } from './hooks/core.ts'
import type { SessionFile } from './hooks/core.ts'

const config = process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude')
const root = process.argv[2] ?? join(config, 'lca-projects')
const projects = join(config, 'projects')

const ls = (dir: string) => {
  try {
    return readdirSync(dir, { withFileTypes: true })
  } catch {
    return []
  }
}

// Line by line: transcripts reach 15 MB.
async function scan(file: SessionFile, path: string) {
  for await (const line of createInterface({ input: createReadStream(path, 'utf8'), crlfDelay: Infinity })) scanLine(file, line)
}

mkdirSync(join(root, 'sessions'), { recursive: true })
let written = 0
let present = 0
for (const p of ls(projects)) {
  if (!p.isDirectory()) continue
  const dir = join(projects, p.name)
  for (const f of ls(dir)) {
    if (!f.isFile() || !f.name.endsWith('.jsonl')) continue
    const sid = f.name.slice(0, -'.jsonl'.length)
    const out = join(root, 'sessions', `${sid}.json`)
    if (existsSync(out)) {
      present++
      continue
    }
    const file = emptySession('')
    await scan(file, join(dir, f.name))
    const cwd = file.cwd
    for (const a of ls(join(dir, sid, 'subagents'))) {
      if (a.isFile() && a.name.endsWith('.jsonl')) await scan(file, join(dir, sid, 'subagents', a.name))
    }
    file.cwd = cwd || file.cwd // a subagent's worktree is not the session's folder
    if (!hasIds(file)) continue
    writeFileSync(out, JSON.stringify(file, null, 2))
    written++
    console.log(`${sid}  ${Object.keys(file.products).length} products  ${Object.keys(file.companies).length} companies  ${file.cwd}`)
  }
}
console.log(`${written} session files written, ${present} already there`)
