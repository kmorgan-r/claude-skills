---
name: ship-repair
description: Ship's delegated repair pass, invoked only when ship hits a repairable gate failure (a P4 lint/type/test gate, or an unresolved P1/P3 CRITICAL after auto-apply). Not invoked by a user directly and not part of the normal pipeline — ship dispatches it via the Skill tool as `ship-repair <phase>` after writing the repair block into .claude-ship-state.json, attempts one bounded, audited fix, and reports a machine-readable verdict.
---

# ship-repair — Ship's Delegated Repair Pass

`ship-repair` exists so the repair logic — and, above all, the gate-weakening
scan below — lives in exactly one place instead of being copied into four
sections of `ship/SKILL.md` and drifting. It is paid for only on the pipelines
that actually trip a gate; ship without a hook into this skill never invokes
it at all.

This skill performs one repair attempt and returns. It does not loop, does
not decide whether a second attempt is warranted, and does not touch the
pipeline's control flow. See **Ownership** at the end.

## 1. Invocation

Ship invokes:

```
ship-repair <phase>
```

where `<phase>` is one of:

- `spec-review` — P1
- `plan-review` — P3
- `implementation` — P4

The failure output is **never** passed as a skill argument — it can be large
(the repair block truncates it to 8KB) and must survive an auto-compaction
that would drop anything living only in the conversation. Instead, read
everything needed from `.claude-ship-state.json`:

- `repair.phase` — must match `<phase>`
- `repair.attempt` — the attempt number at this halt point (read-only — see
  Ownership)
- `repair.failure` — the verbatim failing output
- `repair.signature` — the normalized failure signature (for context only;
  do not recompute or compare it — that is ship's job, part of the
  same-signature ratchet)
- `test_paths` — a **top-level** key of `.claude-ship-state.json`, not part
  of the `repair` block
- `default_branch` — also top-level; needed for `<merge-base>` in §5, rule 4

## 2. Precondition: the tree must be clean on entry

Before anything else, run `git status --porcelain`. If it is non-empty,
stop immediately:

```
REPAIR: refused — dirty tree on entry
```

Touch nothing else — no dispatch, no other state writes, no revert — beyond
clearing `repair.in_flight`, which every terminal verdict does, this one
included (§6). This is `refused`, not `failed`: nothing was attempted, so a
second dispatch would meet the identical dirty tree and gain nothing, while a
`failed` verdict would spend a unit of the global repair budget to discover
that.

(P4 normally reaches this precondition with a clean tree, since
`subagent-driven-development` has already committed by the time its gate
runs. A dirty tree here means something upstream is already wrong.)

## 3. Record the reconciliation manifest before dispatching

Before invoking the repair agent, write `repair.touched_paths` (existing
files) and `repair.created_paths` (new files) into state — **before** the
agent runs, not after it returns.

These two lists are a **reconciliation manifest**, not an authorization
boundary. They exist so that if the repair is interrupted — a crash, a
`/clear`, a fleet respawn — ship's resume logic has something to revert
against, since an interruption reaches none of §6's three verdict exits (no
commit, no revert, no deliberate leave-dirty) and would otherwise leave debris
with no record of what to undo. Because over-including a path here costs
nothing (`git checkout HEAD -- <path>` on a file the agent never touched is a
no-op) while under-including one is the single way reconciliation can miss a
real change, **err wide**:

- **implementation (P4):** the union of (a) every path under the top-level
  `test_paths` key, (b) the failing file(s) named in `repair.signature` /
  `repair.failure`, if any could be parsed, and (c) — whenever (b) is empty
  and §4's fallback authorization therefore fires — the branch-diff set §4
  computes (`git diff --name-only <default_branch>...HEAD`), taken here
  **unfiltered**. Compute that set once, before writing `touched_paths`, and
  derive both widths from that single result — this manifest takes it whole,
  §4's authorization scope takes only its code-shaped subset — never derive
  it twice, since a second derivation could disagree with the first if the
  branch changed in between. Taking the whole set here is what makes the
  superset invariant below hold by construction rather than by inspection:
  the narrow list is a subset of the wide one because it is computed from
  it. `created_paths`: empty unless a new file is already anticipated.
- **spec-review / plan-review (P1/P3):** the single spec or plan file this
  repair was dispatched against. `created_paths`: empty.

**Invariant: `touched_paths` must always be a superset of the agent's
authorization scope, including whatever the scope resolved to via §4's
fallback.** The two lists serve different purposes and must stay different
widths — the reconciliation manifest above is deliberately wide because
over-inclusion is free (`git checkout HEAD -- <path>` on a file the agent
never touched is a no-op), while the authorization scope in §4 is
deliberately narrow because over-inclusion there is exactly what rule 6 and
a human reading a `refused` verdict depend on not happening — but the wide
list must still contain everything the narrow list permits, or an
interrupted repair working under a fallback-authorized file outside
`test_paths` leaves edits that reconciliation's `git checkout HEAD --
<touched_paths>` cannot restore, and the tree comes back dirty for the next
dirty-tree precondition to catch late instead of clean reconciliation
catching it immediately. Do not let a future edit to §4's authorization
logic add a new source of authorized paths without folding it into this
manifest first — this file merely computes them together; nothing else
requires it.

Do not confuse this manifest with the agent's **authorization scope** — a
separate, deliberately narrower list computed in §4. Widening the
reconciliation manifest is safe; widening the authorization scope is not, so
the authorization scope lives only in the agent's prompt and is never written
to state, even though (per the invariant above) it is always computed before,
and folded into, this manifest.

## 4. Dispatch the repair agent

Dispatch one `general-purpose` subagent via the Agent tool. The model depends
on the phase, not on attempt number:

| Phase | Model | Why |
|---|---|---|
| `implementation` (P4) | `sonnet` | Mechanical work against a concrete error string — a failing lint rule, type error, or test assertion. |
| `spec-review` / `plan-review` (P1/P3) | `fable` | The agent is editing a document against a reviewer's judgment, not against a compiler. A weak model here tends to rubber-stamp by rewording the plan or spec until the CRITICAL finding's objection no longer textually matches, which is worse than blocking. |

Before dispatching, compute the agent's **authorization scope** — the files
it may create or modify. This must stay narrow, since it is the boundary
rule 6 (§5) and the human reviewing a `refused` verdict both depend on:

- **implementation (P4):** the failing file(s) named in `repair.failure` (the
  `<file>:<line>` the check reports). If no file can be parsed out of the
  failure text, fall back to the files changed on this branch —
  `git diff --name-only <default_branch>...HEAD`, computed once per §3's
  invariant and used at two widths: §3's reconciliation-manifest component
  (c) takes the whole set, while this authorization scope takes only its
  **code-shaped** paths — excluding `*.test.*` / `*.spec.*` and everything
  document-shaped. **Never** fall back to `test_paths`: authorizing the agent
  to edit only test files hands it exactly the fix the scan exists to catch —
  an unparseable lint or type failure "fixed" by changing the tests instead
  of the source under test. Excluding document-shaped paths is that same
  argument one artifact over: a code gate is never legitimately repaired by
  rewriting the plan that motivated the code, and without the exclusion rule
  6 would authorize exactly that on any branch that also changed docs. The
  exclusion binds the fallback only — a failure that names a document path
  authorizes it, because there the check itself identified the file. If the
  filtered branch diff is empty, do not guess:
  `REPAIR: refused — cannot determine repair scope`.
- **spec-review / plan-review (P1/P3):** the single spec or plan file
  dispatched against — the same file as the reconciliation manifest, since
  rule 6 confines a document repair to exactly one file anyway.

The prompt carries:

- `repair.failure` verbatim — the full failing output, not a summary.
- `repair.attempt` — so the agent knows this may be attempt 2 of 2 and should
  not leave a half-measure for a third attempt that will not come.
- The authorization scope above, stated as the only files the agent may
  create or modify.
- An explicit instruction not to commit: leave all changes uncommitted in
  the working tree; `git add` while preparing a change is fine, but running
  `git commit` is not. ship-repair itself commits — on `applied`, after the
  scan below passes (§6) — never the agent. A commit made by the agent is
  invisible to the scan (§5 reads `git diff HEAD` plus untracked files, both
  of which go empty across a commit) and would let an unscanned diff be
  reported `applied`.
- The gate-weakening scan rules that can fire on **this repair's own diff**
  (§5 below), verbatim, framed as: *these make the repair fail — solve the
  underlying problem instead.* Hand over rules 1–4 and 6 for an `implementation`
  repair; rules 5 and 6 for a `spec-review` / `plan-review` repair. Both repair
  rows sit in the same tier, so "its own tier's rules" is not a live
  distinction — hand over exactly this per-phase set, not all six rules
  regardless of phase, or a document repair that merely quotes rule 2's
  substrings (`.skip`, `@ts-ignore`, and the rest) would be refused for
  quoting the rule.

## 5. The gate-weakening scan

Run this **before** returning `applied`, against the union of:

1. `git diff HEAD` — every tracked change since the last commit, staged or
   unstaged.
2. The untracked (`??`) entries from `git status --porcelain -uall`, **and those
   files' CONTENTS, not just their paths**. `-uall` is required: the default
   collapses a newly created directory to its name alone, so a config written
   inside one is never seen. Reading contents is required because rules 1–3 apply
   to a created file exactly as to an edited one — otherwise an agent moves the
   offending code into a new file topped with `/* eslint-disable */`, and the
   tracked diff reads as a clean refactor.

Both are required, and neither substitutes for the other. A bare `git diff`
shows unstaged changes only and misses anything staged with `git add` —
routine for an agent preparing its own change, not misbehavior, but invisible
to the scan if only unstaged changes are read. `git diff HEAD` fixes that,
but neither form of `git diff` shows untracked files at all, which is exactly
what rule 3 needs: a repair that *creates* `eslint.config.js` or
`vitest.config.ts` rather than editing an existing one would never trigger
rule 3 without the untracked enumeration. This is also why §4 requires the
agent not to commit — if it committed, `git diff HEAD` goes empty too, and
the scan would see nothing at all, returning `applied` on a diff it never
actually read.

Any hit is an immediate `REPAIR: refused`, naming the triggering rule and the
paths involved. This scan runs mechanically regardless of what the prompt
said — a prohibition in the agent's instructions is necessary and not
sufficient, because the dominant failure mode of any auto-repair is
satisfying the gate by disabling it.

1. **Deleted or renamed test file** — any path matching `*.test.*` or
   `*.spec.*` removed by the diff.
2. **Suppression added** — a new occurrence of `.skip`, `.todo`, `.only`,
   `xit(`, `xdescribe(`, `@ts-ignore`, `@ts-expect-error`, or any
   `eslint-disable` form.
3. **Gate config touched** — `.eslintrc*`, `eslint.config.*`, `tsconfig*.json`,
   `vitest.config.*`, `.prettierrc*`, or `package.json` keys `scripts.lint`,
   `scripts.check:types`, `scripts.test`. **Match these on the BASENAME, at any
   depth** — a monorepo keeps `packages/x/tsconfig.json` and
   `services/y/package.json`, and a start-anchored glob misses every one of them,
   making this rule unfireable in exactly the repo shape that needs it most.
   This rule is deliberately
   over-broad: a legitimate fix is sometimes exactly a `tsconfig.json`
   change, and that case is refused anyway. The asymmetry favors a human
   look, and naming the triggering rule lets the human resolve it in one
   edit rather than diagnosing from scratch.
4. **`test_paths` shrank** — re-derive the P4 test-file set and compare
   against the top-level `test_paths` key. Any path present in the stored
   list and absent from the re-derivation means refuse.

   Derive the set with one command, diffing the merge-base directly against
   the working tree:

   ```bash
   git diff --name-only --diff-filter=d <merge-base> -- '*.test.*' '*.spec.*'
   ```

   where `<merge-base>` is `git merge-base <default_branch> HEAD`, and
   `default_branch` is the top-level `default_branch` key of
   `.claude-ship-state.json` — never hardcode `main`. This single command
   replaces an earlier two-command form (`<merge-base>..HEAD` plus a
   separate `HEAD`-to-working-tree diff), which is not used here because it
   cannot detect the case this rule exists to catch — see below.

   **Rule 4 is a comparison, not a selector — do not read `--diff-filter=d`
   as a bug because the command alone never names the deleted file.** It
   isn't meant to. Lowercase `d` deliberately *excludes* deletions, so the
   command's output is "test files that exist right now and were added or
   modified on this branch relative to the merge-base" — the same shape
   ship uses to build `test_paths` in the first place. Because the stored
   list and a fresh re-derivation both use that identical methodology, the
   two are directly comparable, and that comparability is the entire reason
   for the command's shape.

   The signal is the **set difference**: any path present in the stored
   `test_paths` and absent from a fresh re-derivation means that file went
   away. **An empty re-derivation is not a failure of the command — it is
   the strongest possible shrink signal.**

   This is what makes the motivating case detectable: a test file added
   earlier on the branch, then deleted uncommitted by the repair. That file
   is absent from **both** endpoints of the merge-base-to-working-tree diff
   — it did not exist at the merge-base, and the repair's uncommitted delete
   means it does not exist in the working tree either — so it correctly
   does not appear in the fresh re-derivation, for the same underlying
   reason the old two-command form's outputs also failed to show it as a
   deletion. What surfaces the shrink is not the re-derivation alone but its
   comparison against the *stored* `test_paths`, which this same derivation
   produced earlier — before the repair ran, when the file did exist. Do
   not "fix" this by reaching into intermediate commits; the stored list
   already carries that history, because it was produced by the identical
   method at an earlier point in time.

5. **Plan/spec content removed (P1/P3 only)** — a repair may ADD or AMEND.
   At `plan-review` (P3): net task count must not decrease, and no
   acceptance criterion may be deleted. At `spec-review` (P1): the
   task-count form does not apply, since a spec has no numbered tasks —
   instead, no `##`/`###` section may be deleted, and no numbered
   requirement or table row may be removed. Either way, removing the task
   or requirement a reviewer objected to is the plan-side equivalent of
   deleting a failing test.
6. **Out-of-scope path touched, at either phase** — the diff may touch only
   the paths in the **authorization scope** §4 computed for this repair: at
   P1/P3 the single spec or plan file dispatched against; at P4 the failing
   file(s) parsed from `repair.failure`, or the code-shaped branch-diff
   fallback when none could be parsed. Any other path means refuse. This is
   the rule that makes the narrow authorization scope a boundary rather than
   a request — §3 and §4 both call it one, and nothing else here checks it.
   At P1/P3 it is also what makes rule 5's narrow scope safe rather than
   merely narrow: a repair asked to resolve a reviewer's objection to a plan
   has no business editing source.

**Classifying a path as code-shaped or document-shaped.** A path is
**document-shaped** if it is the spec or plan file this repair was dispatched
against, or if it falls under `docs/`. Every other path is **code-shaped**.
Without this rule the split below has no way to decide a stray `.md` file,
and rule 2's bare-substring match (`.skip`, `.todo`, `@ts-ignore`, and the
rest all appear in ordinary prose, including in this very document) would
false-positive on it.

**Scope every rule by the paths the diff actually touches, not by the
repairing phase.** Rules 1–4 run against any code-shaped path in the diff;
rule 5 runs against any document-shaped path; rule 6 runs against every
repair's diff, at both phases, because every repair has an authorization
scope. In the ordinary case that means rules 1–4 fire at `implementation`
and rule 5 at `spec-review` / `plan-review`, but the phases and the rule
groups do not always line up, which is exactly why rule 6 is phase-agnostic:
an `implementation` repair that strays into a spec or plan file and a
document repair that strays into `src/` are the same failure, and rules 1–4
look only at code-shaped paths, so nothing else here would catch the first
one. Scoping rule 6 to documents would leave the narrow P4 authorization
scope that §3 and §4 both call a boundary enforced by the agent's prompt
alone — the containment this scan exists to make mechanical.

**An out-of-scope refusal is not always an attempt to weaken a gate.** A P4
agent may have made a correct fix in an unauthorized place: the type error
reported at `src/foo.ts:14` whose real cause sits in `src/bar.ts`. Rule 6
refuses it regardless, and §6 leaves a `refused` diff uncommitted precisely
so the human can see the candidate fix and adopt it in one look. As with
rule 3, the asymmetry favors a human look over an unaudited edit.

## 6. Working-tree discipline

- **`applied`** — commit the repair: `fix: repair <phase> gate failure
  (attempt N)`, clear `repair.in_flight` (see below), then return the diff
  summary in the verdict line. Ship re-runs the phase's gate only **after**
  ship-repair has already returned; that re-run is ship's job, not this
  skill's, and by the time it happens `in_flight` is already clear.
- **`failed`** — return the diff summary in the verdict line, then revert so
  the next attempt starts from the same clean tree this one saw:

  ```
  git checkout HEAD -- <repair.touched_paths>
  # GUARD: run the clean ONLY when created_paths is NON-EMPTY. An empty list
  # leaves the literal `git clean -f --`, which is a bare `git clean -f`.
  if [ -n "<the created_paths list>" ]; then
    git clean -f -- <repair.created_paths>
  fi
  ```

  Use exactly `git checkout HEAD -- <paths>` for this. Do not use the
  two-dash form without `HEAD` as the revert — that bare form restores from
  the index, which this step never resets, so a repair agent that ran `git add`
  while preparing its own commit (routine, not misbehavior) would have its
  staged change survive the revert, and the next attempt's clean-tree
  precondition would then fail on this attempt's leftover debris. Restoring
  modified files is also not sufficient on its own, since it leaves behind
  anything the agent created; that is why created paths are tracked
  separately and removed with a `git clean -f` scoped to exactly those paths,
  never a bare `git clean` — and **never run at all when there are none.** That
  guard is not defensive tidiness. Substituting an empty list leaves the literal
  `git clean -f --`, and a `--` carrying zero pathspecs does not mean "match
  nothing": git reads it as no path restriction, so with an empty list the
  scoped form *is* the bare form this step was written to avoid. `created_paths`
  is routinely `[]` — a repair that edits existing files creates nothing — so
  the unguarded command is the ordinary case, not an edge case, and what it
  removes is untracked files across the repository that no repair ever touched.
  The guard opens no hole. A file the agent created while `created_paths` said
  `[]` is caught loudly instead: by §2's dirty-tree precondition on the next
  dispatch, and by ship's own residue check, which blocks naming the paths. A
  loud block beats a silent deletion.
- **`refused`** — do not revert. Leave the changes exactly as the agent left
  them, uncommitted, so a human can see precisely what it tried to weaken.
  This is safe only because `refused` blocks the pipeline immediately with no
  further attempt — there is no attempt 2 to contaminate.
- **Clearing `repair.in_flight`** — ship-repair clears it itself, immediately
  before returning its verdict, for **all three** verdicts, `applied`
  included, and for the dirty-tree `refused` in §2 as well. This is not
  "after ship's gate re-run": that re-run belongs to ship and happens after
  ship-repair has already handed back control, so `in_flight` must already
  be clear by then. Ship sets `in_flight` at dispatch; ship-repair is the
  only one that ever clears it, unconditionally, on every terminal verdict.

## 7. Verdict contract

The `REPAIR:` line is the **first line of final output in every case**,
including an internal error (treat an internal error — a state file that
cannot be parsed, an agent dispatch that throws — as `failed`, unless it is
the dirty-tree precondition, which is always `refused`):

```
REPAIR: applied — <diff summary>
REPAIR: failed — <last error>
REPAIR: refused — <triggering rule and paths>
```

The diff summary in `applied` and `failed` is what ship writes into
`repair.history`; keep it short and concrete (files touched, what changed) —
not commentary on how the repair went. An absent or unparseable `REPAIR:`
line is read by ship exactly like an unparseable P6 outcome: it blocks rather
than guessing at a signal it cannot read, so this line must never be buried
under other output.

## Ownership

`ship-repair` never reads or writes `budget_used` or `history`, never writes
`attempt` (it reads `attempt`, per §1, only to know the current attempt
number — for the commit message in §6 and to size its own effort — but never
changes it), and never decides whether to dispatch. It sets `touched_paths`
and `created_paths` (the reconciliation manifest, §3) and clears `in_flight`
on every terminal verdict (§6); ship sets `in_flight` at dispatch and owns
`budget_used`, `attempt`, and `history` entirely. Deciding whether a repair
happens, whether a second attempt is worth it, where the pipeline resumes
after `applied`, and whether the global budget or the same-signature ratchet
has been exhausted are all ship's decisions, made by reading the verdict
line this skill returns — never this skill's.
