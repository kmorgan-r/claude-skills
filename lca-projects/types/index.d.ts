// The state contract other mods may read: self-contained, as the validator requires.

// One follow-up request (a round) on a product, with its onboarding session's state.
export type Req = {
  round: number
  productId: string | null
  status: string // draft | open | closed
  sentAt: string | null
  answered: number
  total: number
  sessionStatus: string // sent | submitted | expired | cancelled
  submittedAt: string | null
  expiresAt: string | null
}

export type ProductRow = { id: string; name: string; lastSeen: string; sessions: number; latest?: Req }

// One Active client company this machine's sessions worked on.
export type Project = {
  id: string
  name: string
  slug: string
  lastSeen: string
  sessions: number
  cwd: string // the launch folder: the brief's launch_cwd once register.tsx read it, else the latest session's
  briefPath: string
  briefExists: boolean
  products: ProductRow[]
  flags: string[]
}

// What the band and the pane draw from. Render hooks read only this; the disk is read on triggers.
export type LcaView = {
  projects: Project[]
  checkedAt: string | null
  note: string | null // the pane's last word: a denied refresh, a tab opened, the copy command
}

declare module 'claude-code' {
  interface PluginState {
    'lca-projects': {
      view: LcaView
    }
  }
}
