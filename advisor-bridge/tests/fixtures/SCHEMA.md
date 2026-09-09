# Transcript record shapes (recorded, not copied)

Measured against the live session transcript on this machine
(`~/.claude/projects/*/<session-id>.jsonl`, 1963 lines at the time of the
final measurement below, 285 `user` records, 490 `assistant` records —
this transcript grew while this task was being worked, so counts were
re-measured at the end rather than mixed from different points in time).
Content was never copied out of the file — only `type` values, key names,
nesting, and counts were recorded. No fixture in this directory contains
any text or value read from a real transcript.

## Top-level record `type` values seen

| `type` | Count | Top-level keys |
|---|---|---|
| `user` | 285 | `parentUuid, isSidechain, promptId, type, message, uuid, timestamp, permissionMode, origin, promptSource, userType, entrypoint, cwd, sessionId, version, gitBranch` |
| `assistant` | 490 | `parentUuid, isSidechain, message, apiBlockIndex, requestId, type, uuid, timestamp, advisorModel, effort, session_id, userType, entrypoint, cwd, sessionId, version, gitBranch` |
| `attachment` | 374 | `parentUuid, isSidechain, attachment, type, uuid, timestamp, rendered, userType, entrypoint, cwd, sessionId, version, gitBranch` |
| `system` | 31 | `parentUuid, isSidechain, type, subtype, durationMs, messageCount, timestamp, uuid, isMeta, userType, entrypoint, cwd, sessionId, version, gitBranch` |
| `ai-title` | 97 | `type, aiTitle, sessionId` |
| `atis-latch` | 99 | `type, atis, sessionId` |
| `bridge-session` | 98 | `type, sessionId, bridgeSessionId, lastSequenceNum, ownerAccountUuid, ownerOrganizationUuid` |
| `file-history-delta` | 14 | `type, messageId, snapshotMessageId, trackingPath, backup, timestamp` |
| `file-history-snapshot` | 7 | `type, messageId, snapshot, isSnapshotUpdate` |
| `last-prompt` | 98 | `type, leafUuid, sessionId` |
| `mode` | 98 | `type, mode, sessionId` |
| `permission-mode` | 98 | `type, permissionMode, sessionId` |
| `queue-operation` | 22 | `type, operation, timestamp, sessionId, content` |
| `relocated` | 76 | `type, sessionId, relocatedCwd` |
| `worktree-state` | 76 | `type, worktreeSession, sessionId` |

The `Top-level keys` column above is **one exemplar record per type**, not
the union of every key that type can carry — most of the additional keys
are conditional and only appear on some records of that type. Recomputed
as a union (hash-set of key names over every record of that type, across
the full 1963-line file), the three types that matter for rendering carry
more keys than a single example shows:

- **`user` (union, 285 records):** adds `isCompactSummary`, `isMeta`,
  `isVisibleInTranscriptOnly`, `queueSkipAttachments`, `slug`,
  `sourceToolAssistantUUID`, `sourceToolUseID`, `toolDenialKind`,
  **`toolUseResult`**, `turnCompanion`. `toolUseResult` is the field the
  spec's Step 5 warns about: it is a sibling of `message`, not nested
  inside `message.content`, and it routinely echoes back full tool output
  (e.g. a file's contents) — which is exactly why a naive whole-record
  grep for `<system-reminder>` produces false positives, and why the
  Step 5 check below reads only `message.content` text.
- **`assistant` (union, 490 records):** adds `apiErrorStatus`,
  `attributionPlugin`, `attributionSkill`, `error`, `isApiErrorMessage`,
  `quotaLimits`, `slug`.
- **`attachment` (union, 374 records):** adds `renderedInHumanTurn`,
  `slug`.

None of these conditional keys affect the fixtures: a renderer that only
reads `type`, `isSidechain`, and `message.content` never needs them.

Only `user`, `assistant`, and `attachment` carry a rendered transcript turn.
Every other type is session bookkeeping with no `message`/content-block
shape at all — a renderer only needs to special-case those three and can
otherwise ignore (drop) any `type` it doesn't recognize.

**`isSidechain`** sits at the top level of the record, as a sibling of
`type` and `message` — not nested inside `message`. It was seen only on
`user`, `assistant`, `system`, and `attachment` records in this transcript
(the bookkeeping types above never carry it). In this transcript every
`user` record had `isSidechain: false` explicitly present (0 `true`, 0
absent, across all 285) — this session did not itself spawn a Task-tool
sidechain, so the `true` and absent-key cases were not observed empirically
here, only confirmed structurally as a plain boolean property that can be
omitted. This is why `sidechain.jsonl` (Step 6) must synthesize the `true`
and absent-key cases by hand rather than by copying an example.

## Where the content blocks sit

`message.content` — for both `user` and `assistant` records. There is no
separate top-level `content[]`; the array (when present) is always nested
under `message`.

`message.content` is **not always an array**. For an ordinary typed user
turn it is a bare JSON string. Measured on this transcript:

| Shape | Count (of 285 `user` records) |
|---|---|
| `message.content` is a string | 23 |
| `message.content` is an array of blocks | 262 |

Assistant records were only observed with array content in this sample.

## Content-block `type` values seen inside `message.content[]`

| Block `type` | Keys |
|---|---|
| `text` | `type, text` |
| `thinking` | `type, thinking, signature` |
| `tool_use` | `type, id, name, input, caller` |
| `tool_result` | `tool_use_id, type, content, is_error` |
| `server_tool_use` | `type, id, name, input` |
| `advisor_tool_result` | `type, tool_use_id, content` |

The plan's fixture helper (`build.ps1`, Step 6) only needs and only
produces the four block types the renderer must handle per the spec:
`text`, `thinking`, `tool_use` (`name`/`input`), `tool_result` (`content`).
`server_tool_use` and `advisor_tool_result` are additional block types
observed on this machine's session (this environment layers its own
advisor tooling on top of stock Claude Code) but are outside the scope of
what Tasks 3-7 render; they are recorded here for completeness only and are
not represented in any fixture.

## `attachment` records — real shape vs. fixture shape

A **real** `attachment` record does NOT have a top-level `message` key.
Its shape is:

```
{ type: "attachment", attachment: { type: <attachment-type>, ...fields }, rendered: [ { content: <string> }, ... ], ...bookkeeping }
```

`attachment.type` values seen in this transcript (26 distinct values),
including the five the spec's settled question names:
`hook_success`, `hook_additional_context`, `environment`,
`session_context`, `total_tokens_reminder` — plus (for completeness, not
used by any fixture): `agent_listing_delta`, `auto_mode`,
`command_permissions`, `compact_file_reference`, `date`,
`deferred_tools_delta`, `deferred_tools_record`, `file`, `hook_cancelled`,
`invoked_skills`, `mcp_instructions_delta`, `model`, `prompt_snapshot`,
`queued_command`, `read_truncation_notice`, `remote_session_change`,
`skill_listing`, `thinking_stripped`.

The `basic.jsonl` fixture (Step 6) deliberately does **not** copy this real
shape. Per the brief, its attachment record is built through the same
`Rec()` helper as the `user`/`assistant` records — `type: "attachment"`
with a top-level `message.content` array containing a `text` block that
carries `ATTACHMENT-MARKER`. This is intentional: it makes the attachment
record structurally renderable (it has a real `text` block a naive
renderer could print), so that a test proving the marker is absent from
rendered output proves the renderer's `type -eq 'attachment'` filter is
doing the work — not an accident of the record having nothing to render in
the first place.

## Does hook output ride inside user records?

**Measured count: 0 user records out of 285 carried `<system-reminder>`
inside their own `message.content` text.** (The spec's prior measurement,
made before this plan was written, found 0 of 119 on a different
216-assistant-turn transcript; this run — 285 `user` records on the
transcript live on this machine now, re-measured at the end of this task
after the transcript grew from an earlier in-task count of 276 — reproduces
the same answer on a larger sample both times.)

The check read only `message.content` text blocks (or the bare string, for
string-shaped content) — never the serialized whole record — per the
brief's warning that a whole-record grep produces false positives (a
`toolUseResult` on a `tool_result` block routinely quotes file content that
itself mentions `system-reminder`).

Every hook payload, environment block, `gitStatus`, and `userEmail` value
that this session emitted rode in a separate `attachment` record instead,
under `attachment.type` values `hook_success`, `hook_additional_context`,
`environment`, `session_context`, `total_tokens_reminder` (see table
above) — confirming the spec's settled answer: **no stripping rule is
needed inside `Format-Turn`.** A renderer that drops any record whose
`type` is not `user`/`assistant` (or is `attachment`, or is a sidechain)
already removes all of this content structurally, before any text
inspection.

Per the brief: count is 0 → note it and move on. No STOP triggered.
