---
name: lca-save
description: Use when saving or updating the brief of an LCA consulting client project (a ClimatePoint company) from the current conversation - at the lca-projects nudge, before closing a client session, or when Kevin says "save the brief" or runs /lca-save [company]. Needs the lca-projects mod's lca_context tool.
---

# lca-save — write the client project's brief

A brief is the memory of one client engagement: what was decided, what blocks, what comes
next. A fresh session launched from the `/lca` pane reads it first. Keep it short, true and
current; it is not a log.

The `lca-projects` mod finds the brief's path. This skill writes the brief from the conversation.

## 1. Find the company

Call `mcp__lca-projects__lca_context`, with `company` set to the argument if one was given (a
name, slug or UUID). It returns JSON:

```json
{
  "sessionId": "<this session>",
  "companies": [
    { "id": "<uuid>", "name": "Acme Motors", "slug": "acme-motors", "status": "active",
      "briefPath": "C:\\Users\\kmorg\\.claude\\lca-projects\\acme-motors\\brief.md",
      "briefExists": true,
      "products": [ { "id": "<uuid>", "name": "...", "lastSeen": "<iso>" } ],
      "followups": { "onboarding": { "status": "...", "submittedAt": null, "expiresAt": null },
                     "requests": [ { "round": 1, "productId": "<uuid>", "status": "open", "answered": 3, "total": 25,
                                     "sessionStatus": "submitted", "sentAt": "...", "submittedAt": "...", "expiresAt": "..." } ] },
      "statusCheckedAt": "<iso>" }
  ],
  "note": "present only when companies is empty"
}
```

- **The tool is missing:** the mod is not loaded in this session. Say so and stop.
- **Denied with "Unknown company":** show the known companies it lists and ask which one.
- **`companies` is empty:** the session's products are not resolved to a company yet. Say what
  `note` says: open `/lca` (which refreshes status from the database) or pass the company, then
  run `/lca-save` again. Stop.
- **More than one company and none named:** ask which one.
- **One company:** use it.

## 2. Read the current brief

If `briefExists` is true, read `briefPath` in full before writing anything.

## 3. Rewrite it from the conversation

Keep what is still true, change what changed, drop what is finished. Use this shape:

```markdown
---
company_id: <id>
company: <name>
products: [{id: <uuid>, name: <name>}, ...]
updated: <YYYY-MM-DD>
sessions: [<session ids>]
launch_cwd: <folder>
---
## Where it stands
One paragraph.

## Products
Per product: id, name, goal or standard (ISO 14067, EN 15804...), current state.

## Decisions
What, who decided, date. A changed decision moves here with its new date; the old one goes.

## Open gates
What blocks, and who owns it.

## Next actions
Concrete steps, with absolute file paths.

## Key files
Absolute paths: emails, research, round.json, report.md.

## Rules
Standing rules for this client, e.g. "no round commit without a fresh export, preview and Kevin's yes".
```

Frontmatter:

- `products`: names and ids from `lca_context`, plus any product already in the brief that is still
  part of the engagement.
- `sessions`: the existing list with `sessionId` appended once.
- `updated`: today.
- `launch_cwd`: keep the brief's value when it has one (Kevin may have set it by hand). Otherwise
  the current working folder. A launched session opens there.

## 4. Write and report

Write the file to `briefPath`, creating its folder if needed. Then report what changed in at
most five lines.

## Never

- Never copy platform follow-up state (rounds, answered counts, link status) into the brief. A
  launched session reads it live with `climatepoint_followup_guide`. The brief records decisions
  *about* rounds, not their state.
- Never write the brief anywhere but `briefPath`.
- Briefs hold client-confidential content. They stay under `~/.claude`. Never copy one into a
  repository, a commit, a PR or an issue.
