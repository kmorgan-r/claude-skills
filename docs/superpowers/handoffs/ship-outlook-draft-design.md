# Ship handoff: outlook-draft

- **Topic:** outlook-draft-design
- **Branch:** `feat/outlook-draft-design` (worktree `.claude/worktrees/ship-outlook-draft-design`)
- **PR:** https://github.com/kmorgan-r/claude-skills/pull/45
- **Spec:** `docs/superpowers/specs/2026-10-06-outlook-draft-design.md`
- **Plan:** `docs/superpowers/plans/2026-10-06-outlook-draft.md`
- **Review profile:** light (`/ship this with only light review`). P1 used a 2-reviewer panel and P3 a 3-reviewer panel. The routine `--diff` re-review was skipped. The coverage and CRITICAL gates were unchanged.
- **Final status:** awaiting a human merge. The conductor never merges.

## Pipeline

| Phase | Outcome |
|---|---|
| P0 init | Worktree mode was used: ollama workers are on and the session ran from a primary checkout. The branch was cut from `main` at dc37d5f. |
| P1 spec review | `REVIEWERS: 2/2` (general-quality=opus, test-quality=sonnet). `FINDINGS: reported C=0 I=8 M=2, applied C=0 I=8 M=1, withheld_minor=1`. |
| P2 plan | 6 tasks. The plan code was replayed red→green from the plan text before execution. |
| P3 plan review | `REVIEWERS: 3/3` (general-quality=opus, test-quality=sonnet, error-handling=sonnet). `FINDINGS: reported C=1 I=7 M=6, applied C=1 I=7 M=4, withheld_minor=2`. The CRITICAL (a post-create network failure could produce a duplicate draft) was applied in ab298db. |
| P4 implementation | See the P4 section below. |
| P5 PR | #45 opened against `main`. |
| P6 fix-pr-reviews | The first bot review on 9e4358e said "✅ No critical issues found" (run 37516932381, success). Loop Complete, no urgent issues, 0 fix rounds. |
| P6.5 db-gates | No DB artifacts (nothing under `supabase/`). `db_gate` is null. |

### P4 detail

Subagent-driven development ran the six tasks:

- Tasks 1–4 and 6 ran on the ollama worker (glm-5.3-flash). Task 5 ran on Anthropic haiku, because it covers sign-in.
- Each task was byte-checked against its brief and reviewed on opus.
- Fix rounds:
  - Task 1 (e510e52): a non-object Graph error body now stays a `GraphError`.
  - Task 4 (bca9a1c): `find` with no query reads the Inbox.

The whole-branch review on opus found one Critical:

- Six post-create paths exited 1 without the "check Outlook Drafts" message, so a retry could create a duplicate draft.
- One fix wave (97cd095) closed them.
- The scoped re-review found everything addressed and no new Critical or Important issues.

The controller also made two doc commits: 1c16f62 (plan drift) and 9e4358e (spec flow and errors).

**Exit gate:** `python -m pytest outlook-draft/tests/test_outlook.py -q` gave 101 passed. There is no `package.json`, so lint and type checks don't apply.

## Rulings made during execution

- **`find` with no query reads `/me/mailFolders/inbox/messages`.** Drafts sort first in `/me/messages`, because a draft's `receivedDateTime` is its creation time. Cost: mail filed by rules into other folders doesn't show up in a no-query `find`.
- **Every failure after the create is exit 3, or carries `MAYBE_CREATED`.** This holds by construction. An unexpected 2xx shape now says "check Drafts" rather than giving a plain error.
- **`login` removes cached accounts before the interactive sign-in.** Cancelling `login` leaves the user signed out; run it again.
- **The broad `except Exception` clauses stay** (post-create, end of `main`, the msal block). The exit-code contract requires every error to come out as stderr JSON.
- **The plan's code blocks for Task 3 and Task 6 stay as historical text.** The shipped code differs where the final fixes landed.

## Leftovers, deliberately not fixed

- Running two skills' test suites in one pytest call collides on `conftest._load`. Each suite passes on its own, which is how the README documents running them.
- A reply merge can put the same address in both To and CC.
- A Partial at the recipients stage skips attachments without listing them as skipped. Only a malformed 2xx can trigger it.
- A 201 `{"id": null}` in new mode, with no attachments, exits 0 with a null link. There is no duplicate risk.
- `--top` accepts 0 and negative numbers.
- The 401 path drops Graph's code and message.
- An msal network failure exits 2 ("setup") rather than 1.
- Test gaps:
  - a 503 that exhausts its retries on a GET;
  - the token-cache location;
  - the error-dict branch of msal;
  - the file shrinking during a chunked upload.
- SKILL.md doesn't name the exit-3 fields (`stage`, `failed_attachments`).
- The README's "DPAPI-encrypted" wording is Windows-only.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` was unset during P6. That was harmless, since the loop needed 0 rounds.

## Live-only checks (not verifiable offline)

Smoke-tested from this branch on 2026-10-06, before merge:

- **Resolved:**
  - `login` works without "Allow public client flows".
  - `$search` with `"` and `\` returns results, no 400.
  - `uniqueBody` honours `Prefer: outlook.body-content-type` (text and HTML).
  - Inline `cid:` images in the quoted thread survive the reply PATCH.
  - A 4.5 MB attachment went through the upload session.
  - The signature logo attaches inline (`image/png`, `contentId` `logo.png`).
- **Still open:**
  - whether msal's `acquire_token_interactive` raises or returns an error dict on its 180-second timeout (both paths print the login command);
  - the 60-second per-chunk `urlopen` timeout on slow uplinks;
  - Exchange `MaxSendSize` for large attachments.

## After merge (manual, with the user)

1. In Entra: register the app as a public client with redirect `http://localhost`. Give it delegated `Mail.ReadWrite` and `People.Read`, and grant admin consent. Never add `Mail.Send`.
2. Install the dependencies: `python -m pip install msal msal-extensions`.
3. Create `~/.claude/outlook-draft/config.json` with `tenant_id` and `client_id`.
4. Junction `~/.claude/skills/outlook-draft` to the **main checkout**, after merge. Never point it at this branch's worktree, and never run `install.ps1` from a branch.
5. Run `! python ~/.claude/skills/outlook-draft/scripts/outlook.py login`.
6. With the user's approval, seed `~/.claude/outlook-draft/signature.html` and `voice.md`.
7. Smoke test: `lookup`, then `find --full`, then a new draft with an attachment, then a reply draft. Open each `webLink`.
