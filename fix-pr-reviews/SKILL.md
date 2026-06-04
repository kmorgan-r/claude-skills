---
name: fix-pr-reviews
description: Fetch and address GitHub PR review comments automatically. Use when you want to fix feedback from code review bots without copy-pasting from GitHub.
---

# Fix PR Reviews

Automatically fetch the most recent code review comments from a GitHub PR and systematically address each issue.

> **Note:** This is a Claude Code skill file. Skills are instruction sets that Claude follows at runtime - they don't require separate "implementation code." When you invoke `/fix-pr-reviews`, Claude reads these instructions and executes the described workflow using its available tools (Bash, Read, Edit, etc.). The bash snippets shown are examples of commands Claude will run, not a separate script to implement.

## When to Use This Skill

Use `/fix-pr-reviews` when:
- Your code review bot (claude) has posted review comments on a PR
- You want to address review feedback without manually copying from GitHub
- You're on a feature branch with an open PR

## Prerequisites

- GitHub CLI (`gh`) must be authenticated
- `jq` must be installed (for JSON parsing in validation scripts)
- Bash-compatible shell (Git Bash on Windows, or native bash on macOS/Linux)
- You must be on a branch with an associated open PR
- The PR must have review comments from the `claude` bot

### Loop Mode Prerequisites

For reliable loop operation, configure early auto-compaction:

```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.) or run before starting
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40
```

This triggers automatic context compaction at 40% usage instead of the default 95%, preventing context exhaustion and degradation during multi-iteration loops.

## Compact Instructions

> **Note:** This section is preserved during auto-compaction and tells Claude what to prioritize.

When context is compacted during `/fix-pr-reviews` loop mode, preserve:
- Current PR number and branch name
- Loop iteration count and max iterations
- Issue history summary: which issues were resolved, re-flagged, or skipped and why
- Files that have been blacklisted due to regression
- Full state is in `.claude-pr-fix-state.json` — re-read it after compaction

## Configuration

The skill supports configuration via environment variables or command arguments:

| Config | Default | Description |
|--------|---------|-------------|
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | `40` (recommended) | Context % at which auto-compaction triggers. **Required for loop mode.** |
| `REVIEW_WORKFLOW` | `claude-code-review.yml` | GitHub Actions workflow that runs the review bot |
| `REVIEW_BOT` | `claude` | GitHub username of the review bot |
| `MAX_LOOP_ITERATIONS` | `5` | Maximum fix iterations in loop mode |
| `LOOP_WAIT_TIMEOUT` | `15` | Minutes to wait for GitHub Action completion |

**State file:** `.claude-pr-fix-state.json` - Tracks loop progress and issue history across compactions and invocations. Use `--reset` to clear history and start fresh.

## Workflow

### Step 1: Detect Current PR

First, identify the PR associated with the current branch:

```bash
# Get current branch name
git branch --show-current

# Find PR for this branch
gh pr list --head "$(git branch --show-current)" --json number,title,url --jq '.[0]'
```

If no PR is found, inform the user and ask them to provide a PR number manually.

### Step 2: Fetch Most Recent Review

Fetch all comments and filter to the most recent one from the `claude` bot:

```bash
# SECURITY: Validate PR_NUMBER is numeric before use in commands
# This prevents command injection if PR_NUMBER is maliciously crafted
if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "❌ Invalid PR number: $PR_NUMBER"
  exit 1
fi

# Get the most recent review comment from claude
gh pr view "$PR_NUMBER" --json comments --jq '
  [.comments[] | select(.author.login == "claude")]
  | sort_by(.createdAt)
  | last
  | {body: .body, createdAt: .createdAt, url: .url}
'
```

If no review from `claude` is found, check for reviews from other common bot names or inform the user.

### Step 3: Parse Review Into Actionable Items

The review typically contains structured sections. Extract items from these patterns:

**Critical Issues Pattern:**
```
## 🔴 Critical Issues
### 1. Issue Title (CRITICAL)
**Location:** src/path/to/file.ts:line
**File:** `src/path/to/file.ts`
```

**High Priority Pattern:**
```
## ⚠️ High Priority Issues
### N. Issue Title
**File:** `src/path/to/file.ts:line-range`
```

**Action Items Pattern:**
```
## 🎯 Action Items (Priority Order)
### MUST FIX BEFORE MERGE
- [ ] **#1:** Description of issue
- [ ] **#2:** Description of issue

### HIGH PRIORITY
- [ ] **#N:** Description
```

### Step 4: Create Todo List

Using the TodoWrite tool, create a prioritized list of items to address.

**Default scope (what gets addressed):**
1. **Critical/Blockers** - Must fix before merge
2. **High Priority** - Should fix before merge
3. **Bug fixes** - Any identified bugs regardless of priority section
4. **Recommended tests** - Test coverage items mentioned in the review

**Skipped by default:**
- Medium priority / Suggestions (nice-to-have improvements)
- Low priority / Nitpicks (minor optional changes)
- Documentation-only suggestions
- Code style preferences

Example todo structure:
```
☐ [CRITICAL] Fix error handling pattern in src/lib/isoReportContent.ts
☐ [CRITICAL] Add missing analytics label in src/utils/reportAnalytics.ts
☐ [HIGH] Add UUID validation to downloadPdf.ts:23-32
☐ [HIGH] Remove duplicate table definition in types.ts
☐ [BUG] Fix race condition in PDF download - downloadPdf.ts:184-195
☐ [TEST] Add unit tests for isoReportContent.ts helper functions
☐ [TEST] Add integration tests for downloadIsoReportPdf
```

### Step 5: Address Issues Incrementally (Context-Aware)

**IMPORTANT: Work incrementally to avoid context exhaustion.**

Before starting any fixes, create a plan summary:

```
## PR Review Fix Plan

**PR:** #2806 - feat: Add ISO 14067 report frontend integration
**Review date:** 2026-01-15T12:39:34Z
**Total items:** 13 (4 critical, 4 high, 2 bugs, 3 tests)

### Items to Address:
1. [CRITICAL] Remove duplicate table definition - types.ts
2. [CRITICAL] Fix column references - migration 20260111
... (brief list only, no full descriptions)
```

**For each todo item:**

1. **Check issue history** — Read `issue_history["file:line"]` from state file
   - If `status == "skipped"`: skip this item, output `⏭️ [N/M] Skipped (previously exhausted): file:line`
   - If `attempts.length >= 3`: auto-skip, set `status: "skipped"`, reason: `"3 attempts exhausted — needs human decision"`
   - If has prior attempts: read all previous `approach` entries. You MUST use a **different** approach. If you cannot think of a genuinely different approach, skip with `outcome: "skipped"` and explain why.
2. **Write approach to state file BEFORE editing code** — Add attempt entry with `approach` describing what you plan to do
3. **Read only the specific file/lines** mentioned in the issue (not entire files if possible)
4. **Implement the fix** following project patterns
5. **Update attempt outcome** — Set to `"pending"` (will be confirmed as `"resolved"` or `"re-flagged"` after next review)
6. **Mark todo as complete** immediately
7. **Summarize what was done** in 1-2 sentences
8. **Move to next item**

**After completing each item, output a brief checkpoint:**
```
✅ [1/13] Fixed duplicate table definition in types.ts
   → Approach: Removed lines 1696-1785 (duplicate iso_report_content)
```

For skipped items:
```
⏭️ [3/13] Skipped: src/handler.ts:80 — "Add input validation"
   → Reason: 2 prior attempts failed, cannot determine different approach without domain context
```

> **Note:** Context is managed automatically via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`. When compaction occurs, the state file ensures continuity.

### State File Persistence

Loop mode uses `.claude-pr-fix-state.json` to track progress and issue history across auto-compactions and invocations:

```json
{
  "pr_number": 2806,
  "branch": "feat/iso-14067-report-frontend",
  "iteration": 2,
  "max_iterations": 5,
  "started_at": "2026-01-15T14:30:00Z",
  "issue_history": {
    "src/handler.ts:80": {
      "priority": "HIGH",
      "description": "Add input validation",
      "status": "re-flagged",
      "attempts": [
        {
          "iteration": 1,
          "approach": "Added zod schema validation at function entry",
          "outcome": "re-flagged"
        }
      ]
    },
    "src/types.ts:45": {
      "priority": "CRITICAL",
      "description": "Removed duplicate table definition",
      "status": "resolved",
      "attempts": [
        {
          "iteration": 1,
          "approach": "Removed lines 1696-1785 (duplicate iso_report_content)",
          "outcome": "resolved"
        }
      ]
    }
  },
  "files_blacklisted": [],
  "previous_urgent_count": 8
}
```

**Status values:** `resolved`, `re-flagged`, `skipped`, `pending`

**At the START of each iteration:**
1. Read `.claude-pr-fix-state.json` if it exists
2. Resume from recorded state, loading `issue_history`
3. For each issue in the new review, check `issue_history[file:line]`:
   - If `status == "skipped"` → skip automatically
   - If `attempts.length >= 3` → auto-skip, set `status: "skipped"`, reason: "3 attempts exhausted"
   - If has prior attempts → must articulate a **different** approach or skip
   - If no history → fix normally

**Before attempting EACH fix (CRITICAL for auto-compaction recovery):**
1. Create or update `issue_history["file:line"]` with priority, description, and status
2. Append a new attempt entry: `{ "iteration": N, "approach": "what you plan to do", "outcome": "pending" }`
3. Write state file IMMEDIATELY using atomic write pattern (see below)

This ensures that if auto-compaction occurs DURING a fix, the approach is already recorded and won't be repeated.

**CRITICAL: Always use atomic writes for state file updates:**
```bash
# WRONG - partial write on crash/compaction causes corruption:
jq '...' .claude-pr-fix-state.json > .claude-pr-fix-state.json

# CORRECT - atomic rename prevents partial writes:
jq '...' .claude-pr-fix-state.json > .claude-pr-fix-state.json.tmp && \
  mv .claude-pr-fix-state.json.tmp .claude-pr-fix-state.json
```
The `mv` command on the same filesystem is atomic on Linux/macOS/Git Bash, so the state file is either fully updated or unchanged - never partially written.

**After EACH successful fix:**
1. Update the latest attempt's `outcome` to `"pending"` (confirmed as `"resolved"` or `"re-flagged"` after next review)
2. Set `issue_history["file:line"].status` to `"pending"`
3. Write updated state to file IMMEDIATELY

**At the END of each iteration:**
1. Increment `iteration` count
2. Update `previous_urgent_count` for regression detection
3. Write state file before commit

This ensures that if auto-compaction occurs mid-iteration, Claude can recover state by reading the file.

### Handoff Summary (For New Conversation)

If you need to continue in a new session, the state file `.claude-pr-fix-state.json` contains all progress including `issue_history` with every approach tried and its outcome. Simply run:

```
/fix-pr-reviews --continue
```

Claude will read the state file (including `issue_history`) and resume from where it left off. No need to paste a handoff summary.

---

## Loop Mode (`--loop`)

Loop mode automatically iterates: fix issues → commit → push → wait for review → check for remaining issues → repeat until clean.

### When to Use Loop Mode

Use `/fix-pr-reviews --loop` when:
- You want fully automated PR review resolution
- Your review bot runs on push via GitHub Actions
- You trust the bot to eventually approve once issues are fixed

### Loop Mode Workflow

```
┌─────────────────────────────────────────────────────────┐
│  ITERATION N                                            │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  1. FIX PHASE                                           │
│     - Fetch latest review from bot                      │
│     - Parse CRITICAL + HIGH + BUG items                 │
│     - Fix each item (standard workflow)                 │
│     - If no urgent items found → EXIT SUCCESS           │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  2. COMMIT & PUSH PHASE                                 │
│     - Stage modified files (explicit list ONLY)         │
│     - Commit: "fix: address PR review (iteration N)"    │
│     - Push to remote: git push                          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  3. WAIT FOR REVIEW PHASE                               │
│     - Find the triggered workflow run                   │
│     - Wait for completion (timeout: 15 min)             │
│     - If timeout → ask user to continue or abort        │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  4. CHECK PHASE                                         │
│     - Fetch NEW review (must be newer than push)        │
│     - Count remaining CRITICAL/HIGH/BUG items           │
│     - If urgent_count == 0 → EXIT SUCCESS               │
│     - If urgent_count > 0 → continue to safety check    │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  5. SAFETY CHECK                                        │
│     - Read state from .claude-pr-fix-state.json         │
│     - Run npm run lint && npm run check:types           │
│     - Check: iteration < MAX_ITERATIONS?                │
│     - Check: current_count <= previous_count?           │
│     - Check: no issues with attempts >= 2?              │
│     - If all OK → LOOP TO ITERATION N+1                 │
│     - If not → stop, state saved for --continue         │
└─────────────────────────────────────────────────────────┘
```

### Step-by-Step Loop Implementation

#### Loop Step 1: Initialize Loop State

**Check for existing state file:**

```bash
# PREREQUISITE: Verify auto-compaction is configured for loop mode
if [ -z "${CLAUDE_AUTOCOMPACT_PCT_OVERRIDE}" ]; then
  echo "⚠️ WARNING: CLAUDE_AUTOCOMPACT_PCT_OVERRIDE is not set"
  echo "Loop mode may fail due to context exhaustion."
  echo ""
  echo "To fix, run:"
  echo "  export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40"
  echo ""
  echo "Then restart Claude Code and retry."
  exit 1
fi

# FIRST: Get current PR number unconditionally (needed for both validation and fresh start)
CURRENT_BRANCH=$(git branch --show-current)
CURRENT_PR=$(gh pr list --head "$CURRENT_BRANCH" --json number --jq '.[0].number // empty')
MAX_ITER="${MAX_LOOP_ITERATIONS:-5}"

if [ -z "$CURRENT_PR" ]; then
  echo "❌ No open PR found for branch '$CURRENT_BRANCH'"
  echo "Create a PR first: gh pr create"
  exit 1
fi

# Check if resuming from previous session
if [ -f ".claude-pr-fix-state.json" ]; then
  echo "Found existing state file - validating..."

  # VALIDATION 1: Check JSON is parseable
  if ! jq empty .claude-pr-fix-state.json 2>/dev/null; then
    echo "⚠️ State file is corrupted (invalid JSON)"
    echo "Creating backup: .claude-pr-fix-state.json.corrupted"
    mv .claude-pr-fix-state.json .claude-pr-fix-state.json.corrupted
    echo "Starting fresh session..."
    ITERATION=1  # Fresh start after corruption
  else
    # VALIDATION 2: Check PR number matches current branch
    STATE_PR=$(jq -r '.pr_number // empty' .claude-pr-fix-state.json)

    # SECURITY: Validate STATE_PR is numeric before using in mv path
    # Prevents path traversal if state file contains malicious pr_number like "../../some/path"
    if [ -n "$STATE_PR" ] && ! [[ "$STATE_PR" =~ ^[0-9]+$ ]]; then
      echo "⚠️ State file has invalid pr_number ($STATE_PR) - treating as corrupted"
      mv .claude-pr-fix-state.json .claude-pr-fix-state.json.corrupted
      STATE_PR=""
      ITERATION=1  # Fresh start after invalid pr_number
    elif [ -n "$STATE_PR" ] && [ "$STATE_PR" != "$CURRENT_PR" ]; then
      echo "⚠️ State file is for PR #$STATE_PR but current branch has PR #$CURRENT_PR"
      echo "Creating backup: .claude-pr-fix-state.json.pr$STATE_PR"
      mv .claude-pr-fix-state.json ".claude-pr-fix-state.json.pr$STATE_PR"
      echo "Starting fresh for PR #$CURRENT_PR..."
      ITERATION=1  # Fresh start after wrong PR
    else
      # VALIDATION 3: Check iteration hasn't exceeded max
      STATE_ITER=$(jq -r '.iteration // 1' .claude-pr-fix-state.json)
      STATE_MAX=$(jq -r '.max_iterations // 5' .claude-pr-fix-state.json)
      if [ "$STATE_ITER" -ge "$STATE_MAX" ]; then
        echo "⚠️ Previous run completed (iteration $STATE_ITER >= max $STATE_MAX)"
        echo "Preserving issue_history, resetting iteration counter..."
        jq --arg started "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
          '.iteration = 1 | .started_at = $started | .previous_urgent_count = null' \
          .claude-pr-fix-state.json > .claude-pr-fix-state.json.tmp && \
          mv .claude-pr-fix-state.json.tmp .claude-pr-fix-state.json
        echo "Reset iteration to 1, preserved $(jq '.issue_history | length // 0' .claude-pr-fix-state.json) issue history entries"
        ITERATION=1
      else
        echo "✅ State file valid for PR #$CURRENT_PR — preserving issue_history"
        # Preserve issue_history but reset iteration for new loop session
        jq --arg started "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
          '.iteration = 1 | .started_at = $started | .previous_urgent_count = null' \
          .claude-pr-fix-state.json > .claude-pr-fix-state.json.tmp && \
          mv .claude-pr-fix-state.json.tmp .claude-pr-fix-state.json
        echo "Reset iteration to 1, preserved $(jq '.issue_history | length // 0' .claude-pr-fix-state.json) issue history entries"
        ITERATION=1
      fi
    fi
  fi
else
  echo "Starting fresh loop session..."
  # Fresh start: set ITERATION to 1 for first iteration commit messages
  ITERATION=1
fi
```

**If `--continue` flag is used (resuming mid-loop):**
1. Read `.claude-pr-fix-state.json`
2. Validate JSON is parseable (if corrupted, backup to `.corrupted` and start fresh)
3. Validate `pr_number` matches current branch's PR (if mismatched, backup to `.prNNN` and start fresh)
4. Load `iteration` as-is (do NOT reset — this is a resume, not a new session)
5. Load `issue_history`, `files_blacklisted`, `previous_urgent_count`
6. Continue from the current iteration

**If `--loop` is used (new loop session) and state file exists for same PR:**
1. Read `.claude-pr-fix-state.json`
2. Validate JSON and PR number (same as above)
3. Preserve `issue_history` and `files_blacklisted` (accumulated knowledge)
4. Reset `iteration` to 1, update `started_at`, clear `previous_urgent_count`
5. Begin new loop session with full history context
6. If state file has old schema (no `issue_history` field), migrate: set `issue_history: {}` and proceed

**If starting fresh:**
Create initial state file using `jq` for proper variable interpolation:

```bash
# Variables are already set unconditionally at the top of the initialization block:
# CURRENT_PR, CURRENT_BRANCH, MAX_ITER

# Create initial state file with actual PR number and branch
# Use jq for safe JSON construction with variable interpolation
jq -n \
  --arg pr "$CURRENT_PR" \
  --arg branch "$CURRENT_BRANCH" \
  --arg started "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson maxiter "$MAX_ITER" \
  '{
    pr_number: ($pr | tonumber),
    branch: $branch,
    iteration: 1,
    max_iterations: $maxiter,
    started_at: $started,
    issue_history: {},
    files_blacklisted: [],
    previous_urgent_count: null
  }' > .claude-pr-fix-state.json.tmp && mv .claude-pr-fix-state.json.tmp .claude-pr-fix-state.json
```

**Output:**
```
## Loop Mode Initialized

**PR:** #<PR_NUMBER>
**Branch:** <current-branch-name>
**Review workflow:** claude-code-review.yml
**Max iterations:** 5 (or MAX_LOOP_ITERATIONS if set)
**State file:** .claude-pr-fix-state.json

Starting iteration 1...
```

#### Loop Step 2: Fix Phase (Standard Workflow)

Run the standard fix workflow (Steps 1-5 above).

**Before fixing, check issue_history for each flagged issue.** If ALL flagged issues already have `status: "skipped"` in `issue_history`, exit the loop immediately:

```
## Loop Complete — Human Review Needed

All N remaining issues have been previously attempted and skipped.
No new fixes to apply. See state file for attempt history.
```

If no urgent issues are found in the review:

```
## Loop Complete - No Urgent Issues

**Iterations:** 0 (no fixes needed)
**Status:** PR review has no critical, high, or bug items

The PR is ready for merge from a code quality perspective.
```

#### Loop Step 3: Commit and Push

**File Tracking Requirements:**

**IMPORTANT: Claude MUST track modified files during the fix phase.**

**Implementation:** At the START of fix phase, initialize an empty list. Each time you use the Edit tool, append the file path to the list:

```
## Internal File Tracking (maintain during fix phase)
MODIFIED_FILES:
- src/lib/parser.ts (edit 1)
- src/api/handler.ts (edit 2)
- src/utils/validation.ts (edit 3)
```

**After completing all fixes**, validate the list before committing:
1. Compare your tracked list against `git diff --name-only`
2. If they differ, investigate - you may have missed tracking a file or made an unintended edit
3. Only proceed with commit if the lists match or discrepancies are understood

**If validation fails (tracked list differs from git diff):**
1. Review the diff: `git diff --name-only`
2. Identify the discrepancy:
   - **Missing from tracked list**: File was edited but not recorded - review the change
   - **Extra in tracked list**: File was tracked but change was reverted - remove from list
3. Choose recovery action:
   - **Include missing files**: "These were intentional, include them in commit"
   - **Discard unintended changes**: `git restore <file>` to revert
   - **Abort iteration**: Exit loop mode for manual review if changes are unclear

After fixing all items in the current iteration:

```bash
# Get list of modified files (if not tracked during fix phase)
MODIFIED_FILES=$(git diff --name-only)

# Validate we have files to commit
if [ -z "$MODIFIED_FILES" ]; then
  echo "⚠️ No files modified - skipping commit"
  # Continue to check phase without pushing
fi

# Stage only the tracked modified files atomically
# IMPORTANT: Do NOT use 'git add -A' as it may stage unrelated files,
# temporary files, build artifacts, or sensitive data.
# Validate file paths before staging (no newlines, null bytes, or git option injection)
# SECURITY: Also verify files still exist to prevent TOCTOU race conditions
SAFE_FILES=""
while IFS= read -r file; do
  # Skip empty lines and validate path characters
  [ -z "$file" ] && continue
  # Reject paths starting with - (could be interpreted as git options)
  case "$file" in -*) echo "⚠️ Skipping suspicious path: $file"; continue;; esac
  # Reject paths with null bytes or control characters
  if printf '%s' "$file" | grep -qE '[[:cntrl:]]'; then
    echo "⚠️ Skipping path with control characters: $file"
    continue
  fi
  # TOCTOU mitigation: Verify file still exists immediately before adding to staging list
  # This reduces the window between validation and staging
  if [ ! -e "$file" ]; then
    echo "⚠️ File no longer exists, skipping: $file"
    continue
  fi
  SAFE_FILES="${SAFE_FILES}${file}"$'\n'
done <<< "$MODIFIED_FILES"

# Stage validated files atomically (minimal TOCTOU window after existence check)
# SECURITY: Add error handling for git add to catch staging failures
if ! printf '%s' "$SAFE_FILES" | git add --pathspec-from-file=-; then
  echo "❌ Git staging failed - a file may have been modified or deleted during processing"
  exit 1
fi

# Create commit message via temp file to avoid shell injection from multi-line content
# This is safer than -m flag which can break with quotes, newlines, or special chars
COMMIT_MSG_FILE=$(mktemp)
# ITERATION variable must be set earlier in the loop (e.g., from state file)
# First line uses printf to safely interpolate the iteration number
printf 'fix: address PR review feedback (iteration %s)\n\n' "$ITERATION" > "$COMMIT_MSG_FILE"
# Append sanitized fix descriptions (remove shell metacharacters, limit length)
# Use printf instead of echo to prevent variable expansion before sanitization
# SECURITY: Use sed with bracket expression for clearer, auditable quote removal
# This is more readable than complex quote escaping: tr -d '"'"'"
printf '%s' "$FIX_DESCRIPTIONS" | tr -d '`$(){}[];<>|&\\' | sed "s/['\"]//g" | head -c 500 >> "$COMMIT_MSG_FILE"
cat >> "$COMMIT_MSG_FILE" << 'COMMIT_EOF'

Co-Authored-By: Claude <noreply@anthropic.com>
COMMIT_EOF

# Create commit using file (avoids all shell escaping issues)
# IMPORTANT: Check for commit failure before proceeding
if ! git commit -F "$COMMIT_MSG_FILE"; then
  echo "❌ Git commit failed - aborting loop iteration"
  rm -f "$COMMIT_MSG_FILE"
  exit 1
fi
rm -f "$COMMIT_MSG_FILE"

# Capture the commit SHA for reliable workflow detection
# Only capture AFTER successful commit to ensure correct SHA
COMMIT_SHA=$(git rev-parse HEAD)

# SECURITY: Validate COMMIT_SHA is a valid git SHA (7-40 hex characters)
# This prevents command injection when COMMIT_SHA is used in gh run list
if ! [[ "$COMMIT_SHA" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "❌ Invalid commit SHA: $COMMIT_SHA"
  exit 1
fi

# Push to trigger new review
if ! git push; then
  echo "❌ Git push failed - aborting loop iteration"
  exit 1
fi
```

Output:
```
✅ Committed and pushed iteration 1 fixes

**Commit:** abc1234
**Files changed:** 4
**Insertions:** 45, Deletions: 12

Waiting for review workflow to complete...
```

#### Loop Step 4: Wait for GitHub Action

```bash
# === VALIDATION MUST BE FIRST - before any variable usage ===

# Get the workflow file name (configurable via env var or --workflow flag)
WORKFLOW="${REVIEW_WORKFLOW:-claude-code-review.yml}"

# SECURITY: Validate workflow name IMMEDIATELY to prevent command injection
# This MUST happen before $WORKFLOW is used in any command
# Only allow alphanumeric, hyphens, underscores, and .yml/.yaml extension
if ! echo "$WORKFLOW" | grep -qE '^[a-zA-Z0-9_-]+\.(yml|yaml)$'; then
  echo "❌ Invalid workflow name: $WORKFLOW"
  echo "   Must match pattern: [a-zA-Z0-9_-]+.(yml|yaml)"
  exit 1
fi

# === END VALIDATION - safe to use $WORKFLOW below ===

# Verify workflow exists
# SECURITY: Use grep -F for fixed-string matching to prevent regex injection
# $WORKFLOW was validated above, but grep -F is still safer for literal matching
if ! gh workflow list --json name,path | grep -qF "$WORKFLOW"; then
  echo "⚠️ Workflow '$WORKFLOW' not found. Available workflows:"
  gh workflow list --json name,path --jq '.[] | "  - \(.path)"'
  exit 1
fi

# COMMIT_SHA is captured in Step 3 before the push
# Using commit SHA is more reliable than timestamps (avoids clock sync issues)

# Poll for workflow run triggered by our commit (with exponential backoff)
MAX_POLL_ATTEMPTS=12  # ~2 minutes total with backoff
POLL_DELAY=5
for i in $(seq 1 $MAX_POLL_ATTEMPTS); do
  # Find workflow run by commit SHA (more reliable than timestamp)
  RUN_ID=$(gh run list \
    --workflow="$WORKFLOW" \
    --commit="$COMMIT_SHA" \
    --json databaseId \
    --jq '.[0].databaseId')

  if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ]; then
    break
  fi

  sleep $POLL_DELAY
  # Exponential backoff: 5s, 10s, 20s, 30s (capped)
  POLL_DELAY=$((POLL_DELAY * 2))
  [ $POLL_DELAY -gt 30 ] && POLL_DELAY=30
done

if [ -z "$RUN_ID" ] || [ "$RUN_ID" == "null" ]; then
  echo "⚠️ No workflow run detected after 2 minutes for commit $COMMIT_SHA"
  # Offer user options to continue or abort
  exit 1
fi

# SECURITY: Validate RUN_ID is numeric to prevent command injection
# RUN_ID comes from gh run list output - must be a positive integer
if ! [[ "$RUN_ID" =~ ^[0-9]+$ ]]; then
  echo "❌ Invalid run ID: $RUN_ID"
  exit 1
fi

# Watch the run until completion (timeout from LOOP_WAIT_TIMEOUT env var, default 15 min)
# SECURITY: Validate LOOP_WAIT_TIMEOUT is numeric before arithmetic expansion
TIMEOUT_MINUTES="${LOOP_WAIT_TIMEOUT:-15}"
if ! [[ "$TIMEOUT_MINUTES" =~ ^[0-9]+$ ]]; then
  echo "⚠️ Invalid LOOP_WAIT_TIMEOUT value: $TIMEOUT_MINUTES (using default 15)"
  TIMEOUT_MINUTES=15
fi
TIMEOUT_SECONDS=$((TIMEOUT_MINUTES * 60))
timeout $TIMEOUT_SECONDS gh run watch "$RUN_ID" --exit-status
```

**Progress output during wait:**
```
⏳ Waiting for review workflow...
   Run ID: 12345678
   Workflow: claude-code-review.yml
   Status: in_progress
   Elapsed: 2m 15s
```

**On completion:**
```
✅ Review workflow completed

**Run ID:** 12345678
**Duration:** 3m 42s
**Status:** success

Fetching new review...
```

**On timeout:**
```
⚠️ Review workflow timeout after 15 minutes

**Run ID:** 12345678
**Status:** still running

Options:
1. Keep waiting (extend timeout by 10 min)
2. Check manually and resume: /fix-pr-reviews --loop --continue
3. Abort loop mode
```

**Timeout handling implementation:**
When timeout occurs, Claude should use AskUserQuestion to present these options:
- **Option 1 (Keep waiting):** Add 10 minutes to TIMEOUT_SECONDS and re-run `gh run watch`
- **Option 2 (Check manually):** Provide the loop state summary and exit. User can resume later with `--continue`
- **Option 3 (Abort):** Exit loop mode, report progress so far, do NOT commit any pending changes

#### Loop Step 5: Fetch and Check New Review

```bash
# Get the timestamp of our push
PUSH_TIME=$(git log -1 --format=%cI)

# SECURITY: Validate PUSH_TIME is ISO 8601 format to prevent jq injection
# Format: YYYY-MM-DDTHH:MM:SS+HH:MM or YYYY-MM-DDTHH:MM:SSZ
# This prevents maliciously crafted GIT_COMMITTER_DATE from injecting jq code
if ! [[ "$PUSH_TIME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([+-][0-9]{2}:[0-9]{2}|Z)$ ]]; then
  echo "❌ Invalid timestamp format: $PUSH_TIME"
  exit 1
fi

# SECURITY: Validate PR_NUMBER is numeric before use in commands
# This prevents command injection if PR_NUMBER is maliciously crafted
if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "❌ Invalid PR number: $PR_NUMBER"
  exit 1
fi

# Fetch comments newer than our push
gh pr view "$PR_NUMBER" --json comments --jq "
  [.comments[]
   | select(.author.login == \"claude\")
   | select(.createdAt > \"$PUSH_TIME\")]
  | sort_by(.createdAt)
  | last
"
```

**Reconcile review with issue_history:**

After fetching the new review, compare each flagged issue against `issue_history`:

1. **Issues in new review that match a `file:line` in `issue_history`:**
   - Update `status` from `"pending"` to `"re-flagged"`
   - The issue was not resolved by the previous approach

2. **Issues in `issue_history` with `status: "pending"` NOT in new review:**
   - Update `status` to `"resolved"`
   - The previous approach worked

3. **New issues not in `issue_history`:**
   - These are genuinely new findings (possibly regressions from fixes)
   - Add to `issue_history` with empty `attempts` array

4. **Write updated state file** after reconciliation

> **Note on `file:line` matching:** Line numbers may shift after fixes. Match by file path first, then by closest line number and description similarity. If ambiguous, treat as a new issue. This is a known limitation — document in commit message if matching is uncertain.

This reconciliation is what gives the fixer context — it knows which approaches worked and which didn't.

**Check for urgent issues:**

> **IMPORTANT - SECURITY NOTE:** The shell-based fallback below exists only for edge cases.
> Claude MUST use its LLM parsing capabilities as the PRIMARY method for analyzing review content.
> Shell parsing of untrusted input (review comments) carries inherent injection risks even with sanitization.

Rather than relying on fragile grep patterns, Claude should **parse the review body directly** using its language understanding capabilities:

1. **Extract the review body** from the fetched comment
2. **Identify structured sections** (## headings with priority indicators)
3. **Count issues per category** based on section headers and content:
   - Critical: Sections with "Critical", "Blocker", "MUST FIX"
   - High: Sections with "High Priority", "Should Fix"
   - Bugs: Items mentioning "bug", "race condition", "memory leak", etc.
4. **Validate the review is non-empty** before analysis

This approach is more reliable than regex matching because:
- It handles variations in review formatting
- It avoids false positives from discussions about keywords
- It works regardless of terminal encoding for emojis

**Fallback pattern matching (if needed):**
```bash
# First, check the workflow run status to distinguish success cases
WORKFLOW_STATUS=$(gh run view "$RUN_ID" --json conclusion --jq '.conclusion')

# Validate review body exists and is not null/empty
if [ -z "$REVIEW_BODY" ] || [ "$REVIEW_BODY" == "null" ] || [ "$REVIEW_BODY" == "{}" ]; then
  # Distinguish between "no issues" vs "workflow failed"
  if [ "$WORKFLOW_STATUS" == "success" ]; then
    echo "✅ Workflow succeeded with no review comment - likely clean!"
    URGENT_TOTAL=0  # Treat as success
  elif [ "$WORKFLOW_STATUS" == "failure" ] || [ "$WORKFLOW_STATUS" == "cancelled" ]; then
    echo "❌ Workflow $WORKFLOW_STATUS - review may not have run"
    URGENT_TOTAL=-1  # Signal error state
  else
    echo "⚠️ Empty review body with workflow status: $WORKFLOW_STATUS"
    URGENT_TOTAL=-1  # Signal unknown state
  fi
else
  # SECURITY: Sanitize REVIEW_BODY before any shell processing
  # Remove shell metacharacters to prevent command injection from malicious review content
  # WARNING: This sanitization removes common shell metacharacters but may not catch all
  # edge cases (*, ?, ~, !, #, newlines, glob patterns). Claude's LLM parsing capabilities
  # are the PRIMARY and SAFER method for analyzing review content. This shell fallback
  # exists only for edge cases where LLM parsing is unavailable.
  # Use sed for clearer, more auditable quote removal
  SAFE_BODY=$(printf '%s' "$REVIEW_BODY" | tr -d '`$(){}[];<>|&\\*?~!#' | sed "s/['\"]//g")

  # Trim whitespace and check for meaningful content
  TRIMMED=$(printf '%s' "$SAFE_BODY" | tr -d '[:space:]')
  if [ ${#TRIMMED} -lt 50 ]; then
    echo "⚠️ Review body too short to contain issues"
    URGENT_TOTAL=0
  else
    # VALIDATION: Check for structural markers before trusting grep counts
    # A valid review should have at least one "## " heading (e.g., "## High-Priority")
    # or mention "Action Items". Without these, the body may be malformed or unexpected format.
    HAS_STRUCTURE=$(printf '%s' "$SAFE_BODY" | { grep -cE "^## |Action Items" || true; })
    if [ "$HAS_STRUCTURE" -eq 0 ]; then
      echo "⚠️ Review body has no expected structural markers (## headings or Action Items)"
      echo "   This may indicate unexpected format - manual inspection recommended"
      URGENT_TOTAL=-1  # Signal parse failure, not success
    else
      # Count issues using grep -c with proper zero-handling
      # NOTE: grep -c prints 0 and exits with code 1 on no matches.
      # Using "|| true" prevents the exit code from triggering error handling,
      # while grep -c still outputs the correct count (0 for no matches).
      # SECURITY NOTE: Patterns are hardcoded, not user-controlled.
      CRITICAL_COUNT=$(printf '%s' "$SAFE_BODY" | { grep -ciE "^## .*(Critical|Blocker|MUST FIX)" || true; })
      HIGH_COUNT=$(printf '%s' "$SAFE_BODY" | { grep -ciE "^## .*(High Priority)" || true; })
      BUG_COUNT=$(printf '%s' "$SAFE_BODY" | { grep -ciE "^### .*(Bug|Potential Bug)" || true; })
      URGENT_TOTAL=$((CRITICAL_COUNT + HIGH_COUNT + BUG_COUNT))
    fi
  fi
fi

# Handle error/unknown state - MUST stop loop on failure
# SECURITY: Use numeric comparison -eq instead of string comparison ==
# This ensures proper integer handling and avoids unexpected behavior with whitespace
if [ "$URGENT_TOTAL" -eq -1 ]; then
  echo "❌ Cannot determine issue count - stopping loop to prevent broken commits"
  echo ""
  echo "Options:"
  echo "1. Check PR manually: gh pr view $PR_NUMBER --web"
  echo "2. Resume later: /fix-pr-reviews --loop --continue"
  echo "3. Run without loop: /fix-pr-reviews"
  # Exit loop mode - do NOT continue with unknown state
  exit 1
fi
```

**Decision output:**
```
## Review Check (Iteration 1 → 2)

**New review received:** 2026-01-15T14:45:23Z
**Urgent issues remaining:** 3
  - Critical: 0
  - High: 2
  - Bugs: 1

Continuing to iteration 2...
```

Or if clean:
```
## Loop Complete - All Clear!

**Total iterations:** 2
**Total fixes applied:** 8
**Final review:** No critical, high, or bug items

### Summary of All Fixes:
**Iteration 1:** (5 items)
- ✅ Fixed duplicate table definition
- ✅ Added UUID validation
- ✅ Fixed race condition
- ✅ Added error handling
- ✅ Fixed memory leak

**Iteration 2:** (3 items)
- ✅ Added input sanitization
- ✅ Fixed edge case in parser
- ✅ Added missing null check

The PR is ready for merge!
```

Or if all remaining issues are skipped:
```
## Loop Complete — Human Review Needed

**Iterations:** 3
**Resolved:** 5 issues
**Skipped (needs human):** 3 issues
  - [HIGH] src/handler.ts:80 — "Add input validation" (2 attempts, skipped: reviewer disagrees on validation strategy)
  - [BUG] src/parser.ts:45 — "Null pointer risk" (3 attempts exhausted)
  - [HIGH] src/auth/middleware.ts:12 — "Session token handling" (1 attempt, skipped: cannot determine correct approach without domain context)

These issues require manual intervention. Review the state file for full attempt history:
`cat .claude-pr-fix-state.json | jq '.issue_history | to_entries[] | select(.value.status == "skipped")'`
```

#### Loop Step 6: Safety Checks

**MANDATORY: Perform ALL safety checks before each iteration.**

Before starting the next iteration, Claude MUST:

1. **Read state file** - Load `.claude-pr-fix-state.json` to get current state
2. **Run code quality checks** - Execute `npm run lint && npm run check:types`
3. **Compare issue counts** - Check for regression (more issues than before)
4. **Check issue history** - Skip issues with 3+ attempts or `status == "skipped"`
5. **Check "all skipped"** - If every remaining issue is skipped, exit loop
6. **Verify iteration count** - Ensure we haven't exceeded MAX_ITERATIONS

```
## Safety Check (Before Iteration 3)

**Iterations completed:** 2 of 5 max
**Resolved:** 6 issues
**Skipped:** 1 issue (3 attempts exhausted)
**Active issues remaining:** 2
**Previous urgent count:** 8
**Current urgent count:** 3 (improving, 1 skipped)
**Recurring issues:** None with actionable approaches left

✅ Safe to continue - starting iteration 3...
```

**Pre-commit validation (CRITICAL):**
```bash
# MUST pass before committing - catches regressions
npm run lint && npm run check:types
```

**If lint/type checks fail after fixes:**

The code got WORSE. Do NOT commit. Follow this recovery procedure:

1. **Identify the problematic fix:**
   ```bash
   # See all changes since last commit
   git diff

   # Check which files have lint errors
   npm run lint 2>&1 | grep "error" | head -20
   ```

2. **Revert the problematic file(s):**
   ```bash
   # Revert ONLY the specific file that caused lint failure
   git checkout HEAD -- src/problematic-file.ts
   ```

   **WARNING:** Do NOT use `git checkout -- .` here - it would discard ALL fixes made this iteration, not just the problematic one. Only revert the specific file(s) that caused the lint/type failure.

3. **Update state file:**
   - Add a failed attempt to `issue_history["file:line"].attempts` with `outcome: "lint-failure"`
   - If 3+ attempts, set `status: "skipped"` with reason
   - Add the file to `files_blacklisted` if multiple issues in that file failed
   - Write the updated state file

4. **Continue with remaining issues:**
   - Skip the failed issue (it will be auto-skipped due to attempt history)
   - Proceed to next issue in the queue

**Example recovery output:**
```
❌ Lint check failed after fix

**Failed check:** npm run lint
**Errors in:** src/lib/parser.ts (2 errors)

**Recovery:**
1. Reverting src/lib/parser.ts: git checkout HEAD -- src/lib/parser.ts
2. Marking issue as [SKIP-MANUAL]: src/lib/parser.ts:45
3. Updating state file with failed attempt
4. Continuing with 3 remaining issues...
```

**Regression detection:**

**IMPORTANT:** Handle the `URGENT_TOTAL=-1` sentinel value before comparing counts.

When `URGENT_TOTAL=-1` (parse failure), do NOT update `previous_urgent_count` and do NOT perform regression comparison:
```bash
if [ "$URGENT_TOTAL" -eq -1 ]; then
  echo "⚠️ Could not parse review body structure - skipping regression check this iteration"
  echo "Prompting user for manual inspection..."
  # Do NOT update previous_urgent_count in state file
  # Do NOT compare against previous count (would produce false positive/negative)
  # Ask user whether to continue or abort
fi
```

If `URGENT_TOTAL >= 0`, compare `previous_urgent_count` from state file with current count.

**Handle null/empty previous_urgent_count on first iteration:**
```bash
# Load previous count from state file (null -> empty string via jq)
PREV_COUNT=$(jq -r '.previous_urgent_count // empty' .claude-pr-fix-state.json)

# On first iteration, previous_urgent_count is null - skip regression check
if [ -z "$PREV_COUNT" ] || [ "$PREV_COUNT" == "null" ]; then
  echo "First iteration - no previous count to compare (skipping regression check)"
  # Proceed without regression comparison
elif [ "$URGENT_TOTAL" -gt "$PREV_COUNT" ]; then
  echo "⚠️ REGRESSION DETECTED"
  # Handle regression...
fi
```

Regression output when detected:
```
⚠️ REGRESSION DETECTED

Previous urgent count: 5
Current urgent count: 8

Fixes introduced MORE issues. Actions:
1. Identify which fix caused regression (check git diff)
2. Revert problematic changes: git checkout HEAD -- <file>
3. Blacklist file in state: add to files_blacklisted
4. Continue with remaining issues
```

**Same-issues detection:**
Check `issue_history` in state file. If an issue has 3+ attempts or `status == "skipped"`:
```
⚠️ Skipped issues (exhausted approaches or manually skipped):
- [HIGH] src/lib/parser.ts:45 - "Complex type inference" (3 attempts exhausted)
  Approaches tried: zod validation, manual type guard, schema inference
- [BUG] src/api/handler.ts:120 - "Potential race condition" (2 attempts, skipped: cannot determine correct approach)
  Approaches tried: mutex lock, queue-based serialization

These issues require manual intervention. They will not block loop termination.
Continuing with actionable issues...
```

**If max iterations reached:**
```
## Loop Stopped - Max Iterations Reached

**Iterations completed:** 5 of 5 max
**Issues remaining:** 2 (1 high, 1 bug)

State saved to .claude-pr-fix-state.json

**Recommendation:** Review remaining issues manually or run
`/fix-pr-reviews --continue` to resume with fresh context.
```

**If auto-compaction occurs:**
Auto-compaction is handled automatically by `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`.
After compaction, Claude will re-read `.claude-pr-fix-state.json` to restore state.
No manual intervention needed.

---

## Parsing Rules

### File Location Extraction

The review may specify file locations in several formats:

| Format | Example | Extracted |
|--------|---------|-----------|
| `**Location:**` | `**Location:** src/lib/file.ts:45` | `src/lib/file.ts`, line 45 |
| `**File:**` | `**File:** \`src/lib/file.ts:10-20\`` | `src/lib/file.ts`, lines 10-20 |
| Inline code | `in \`src/lib/file.ts\`` | `src/lib/file.ts` |
| Parenthetical | `(line 145)` or `(lines 34-65)` | line number(s) |

### Priority Classification

| Review Section | Priority | Todo Prefix | Addressed by Default |
|----------------|----------|-------------|----------------------|
| Critical Issues | Highest | `[CRITICAL]` | Yes |
| Blockers | Highest | `[CRITICAL]` | Yes |
| MUST FIX BEFORE MERGE | Highest | `[CRITICAL]` | Yes |
| High Priority | High | `[HIGH]` | Yes |
| Should Fix | High | `[HIGH]` | Yes |
| Potential Bugs | Bug | `[BUG]` | Yes |
| Bug fixes | Bug | `[BUG]` | Yes |
| Test Coverage | Test | `[TEST]` | Yes |
| Missing Tests | Test | `[TEST]` | Yes |
| Recommended Tests | Test | `[TEST]` | Yes |
| Medium Priority | Medium | `[MEDIUM]` | **No** |
| Suggestions | Medium | `[MEDIUM]` | **No** |
| Nice to Have | Low | `[LOW]` | **No** |
| Nitpicks | Low | `[LOW]` | **No** |

### Bug Detection Patterns

Also extract items that mention bugs, even if not in a "Critical" section:

| Pattern | Example |
|---------|---------|
| "BUG:" prefix | `### BUG: Race condition in...` |
| "Potential Bug" | `### Potential Bug: Null pointer...` |
| "will cause" + error | `This will cause a runtime error` |
| "race condition" | `Race condition in PDF download` |
| "memory leak" | `Memory leak from unreleased blob URLs` |
| "null/undefined" issues | `Null reference when...` |

### Test Detection Patterns

Extract test recommendations from these patterns:

| Pattern | Example |
|---------|---------|
| "Missing Tests" section | `## Missing Tests` |
| "Test Coverage" section | `## 🧪 Test Coverage` |
| "No test files" | `No test files included for...` |
| "Recommended Tests" | `**Recommended Tests:**` |
| "should have tests" | `This function should have tests` |
| Test file suggestions | `Add to src/lib/__tests__/file.test.ts` |

### Skip These Items (Default)

Do not create todos for:
- Items already marked with `[x]` (completed)
- Items under "Positive Observations" or "Strengths" sections
- Medium priority / Suggestions (use `--all` to include)
- Low priority / Nitpicks (use `--all` to include)
- Documentation-only suggestions (use `--include-docs` to include)
- Code style preferences without functional impact

---

## Optional Arguments

The skill supports optional arguments:

| Argument | Description | Example |
|----------|-------------|---------|
| `<PR_NUMBER>` | Specify PR number directly | `/fix-pr-reviews 2806` |
| `--loop` | **Enable loop mode** - auto-iterate until clean | `/fix-pr-reviews --loop` |
| `--workflow=<name>` | Specify review workflow (default: claude-code-review.yml) | `/fix-pr-reviews --loop --workflow=claude-review.yml` |
| `--max-iterations=<N>` | Max loop iterations (default: 5) | `/fix-pr-reviews --loop --max-iterations=3` |
| `--all` | Include medium/low priority items too | `/fix-pr-reviews --all` |
| `--critical-only` | Only critical/blockers (skip high, bugs, tests) | `/fix-pr-reviews --critical-only` |
| `--no-tests` | Skip writing tests | `/fix-pr-reviews --no-tests` |
| `--list-only` | Show issues without fixing | `/fix-pr-reviews --list-only` |
| `--include-docs` | Include documentation suggestions | `/fix-pr-reviews --include-docs` |
| `--continue` | Resume from saved state file (`.claude-pr-fix-state.json`) | `/fix-pr-reviews --continue` |
| `--dry-run` | Loop mode: show what would happen without committing | `/fix-pr-reviews --loop --dry-run` |
| `--reset` | Clear issue history and state file before starting | `/fix-pr-reviews --loop --reset` |

**Default behavior:** Critical + High + Bugs + Tests (skip Medium/Low/Docs)

**`--reset` flag behavior:**
When `--reset` is passed, delete the existing `.claude-pr-fix-state.json` before any other initialization:
```bash
if [ "$RESET_FLAG" == "true" ] && [ -f ".claude-pr-fix-state.json" ]; then
  echo "🔄 Resetting issue history and state file..."
  # Backup before deleting (safety net)
  cp .claude-pr-fix-state.json ".claude-pr-fix-state.json.backup-$(date +%s)"
  rm .claude-pr-fix-state.json
  echo "✅ State cleared. Starting fresh."
fi
```
This is the only way to clear `issue_history` for a PR. Without `--reset`, history persists across all invocations on the same PR.

---

## Example Sessions

### Standard Mode Example

```
User: /fix-pr-reviews

Claude: I'll fetch the most recent code review for your current PR.

[Runs: git branch --show-current]
→ feat/iso-14067-report-frontend

[Runs: gh pr list --head feat/iso-14067-report-frontend ...]
→ PR #2806: feat: Add ISO 14067 report frontend integration

[Runs: gh pr view 2806 --json comments --jq '...']
→ Found review from claude at 2026-01-15T12:39:34Z

Parsing review...

## PR Review Fix Plan

**PR:** #2806 - feat: Add ISO 14067 report frontend integration
**Review date:** 2026-01-15T12:39:34Z
**Total items:** 13 (4 critical, 4 high, 2 bugs, 3 tests)

### Items to Address:
1. [CRITICAL] Remove duplicate table definition - types.ts
2. [CRITICAL] Fix column references - migration 20260111
...

[Fixes items one by one with checkpoints]

✅ All 13 items addressed!

Would you like me to commit these changes?
```

### Loop Mode Example

```
User: /fix-pr-reviews --loop

Claude: Starting loop mode for PR review fixes.

## Loop Mode Initialized

**PR:** #2806
**Branch:** feat/iso-14067-report-frontend
**Review workflow:** claude-code-review.yml
**Max iterations:** 5
**State file:** .claude-pr-fix-state.json

Starting iteration 1...

---

## Iteration 1

[Fetches review, finds 8 urgent items]
[Checks issue_history — all new, no prior attempts]
[Fixes all 8 items, recording approach for each]

✅ [1/8] Fixed duplicate table — approach: "Removed lines 1696-1785 (duplicate iso_report_content)"
✅ [2/8] Added UUID validation — approach: "Added zod UUID schema at function entry"
...
✅ Iteration 1 complete - 8 items fixed, 0 skipped

**Committing and pushing...**

✅ Pushed commit abc1234

**Waiting for review workflow...**

✅ Run 12345678 completed (3m 12s)

**Reconciling review with issue history...**
- src/types.ts:45 — not re-flagged → status: resolved
- src/handler.ts:80 — re-flagged → status: re-flagged (1 prior attempt)

Urgent issues remaining: 2 (0 critical, 1 high, 1 bug)

---

## Iteration 2

[Fetches new review, finds 2 re-flagged items]
[Checks issue_history — both have 1 prior attempt]
[Must try different approach for each]

✅ [1/2] Fixed handler validation — NEW approach: "Switched from zod to manual type guard with early return"
✅ [2/2] Fixed parser null check — NEW approach: "Added optional chaining instead of explicit null check"

✅ Iteration 2 complete - 2 items fixed, 0 skipped

**Committing and pushing...**

✅ Pushed commit def5678

**Waiting for review workflow...**

✅ Run 12345679 completed (2m 58s)

**Reconciling review with issue history...**
- All issues resolved

Urgent issues remaining: 0

---

## Loop Complete - All Clear!

**Total iterations:** 2
**Total fixes applied:** 10
**Skipped:** 0

### Summary:
**Iteration 1:** Fixed 8 items (4 critical, 2 high, 2 bugs)
**Iteration 2:** Fixed 2 re-flagged items with different approaches

The PR is ready for merge!
```

---

## Handling Edge Cases

### No PR Found
```
Could not find an open PR for branch 'feature-xyz'.
Options:
1. Provide PR number: /fix-pr-reviews 123
2. Create a PR first: gh pr create
```

### No Reviews Found
```
No review comments from 'claude' found on PR #2806.
The PR has comments from: vercel, github-actions

Would you like me to check reviews from a different author?
```

### Already Fixed Items
If an issue mentions a file/line that has been modified since the review:
```
Note: src/lib/file.ts has been modified since this review (3 commits ago).
This issue may already be addressed. Skipping...
```

### Loop Mode: Workflow Not Found
```
⚠️ Could not find workflow 'claude-code-review.yml'

Available workflows:
- ci.yml
- deploy.yml
- code-review.yml

Please specify: /fix-pr-reviews --loop --workflow=code-review.yml
```

### Loop Mode: No New Review After Push
```
⚠️ No new review found after 5 minutes

The review workflow completed but no new comment from 'claude' was posted.
This might mean:
1. The bot only comments when there are issues (good sign!)
2. The workflow failed silently
3. The bot uses a different comment mechanism

Options:
1. Check PR manually: gh pr view 2806 --web
2. Assume clean and exit loop mode
3. Wait longer for review (extend by 5 min)
```

### Loop Mode: Same Issues Persist
```
⚠️ Re-flagged issues with multiple attempts:

1. [HIGH] src/auth/handler.ts:80 — "Refactor authentication flow"
   Attempt 1: Added middleware guard → re-flagged
   Attempt 2: Switched to decorator pattern → re-flagged
   Attempt 3: Auto-skipped (3 attempts exhausted)

2. [BUG] src/cache/store.ts:45 — "Potential memory leak"
   Attempt 1: Added cleanup in useEffect → re-flagged
   Skipped: Cannot determine correct approach without domain context

These issues are now skipped and won't block loop termination.
The loop will exit when no actionable issues remain.
```

---

## Best Practices

1. **Set auto-compaction**: Before loop mode, ensure `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40` is set
2. **Review before fixing**: Read through all parsed items before starting fixes
3. **Run quality checks**: Always run `npm run lint && npm run check:types` before committing
4. **Commit incrementally**: Consider committing after each critical fix
5. **Re-run review**: After fixing all items, consider re-running `/review` to verify
6. **Update PR**: Push changes and wait for new review if needed
7. **Loop mode**: Use `--dry-run` first to preview what the loop will do
8. **Workflow config**: Verify your review workflow name before using `--loop`
9. **Monitor iterations**: If the loop exceeds 3 iterations, consider manual review
10. **Reset when stuck**: Use `/fix-pr-reviews --loop --reset` to clear issue history and retry. State auto-clears when switching PRs.

---

## Troubleshooting

### Workflow Not Triggering

**Symptoms:** Push completes but no workflow run detected after 2 minutes.

**Causes & Solutions:**
1. **Branch protection rules** - Check if your branch requires PR approval before workflows run
2. **Workflow path filters** - Verify workflow triggers on your changed files (check `paths:` in workflow YAML)
3. **Workflow disabled** - Check GitHub Actions settings: `gh workflow list`
4. **Rate limiting** - GitHub may delay workflow starts under heavy load; try extending timeout

### Same Issues Every Iteration

**Symptoms:** The review bot keeps flagging the same issues after you fix them.

**Causes & Solutions:**
1. **Issue history prevents infinite loops** - The `issue_history` tracks every approach tried. After 3 attempts, issues are auto-skipped. If the loop still runs too long, use `--reset` and `--critical-only`
2. **Different interpretation** - Your fix may not match what the bot expects; the approach history helps Claude try genuinely different strategies
3. **Cached review** - The bot may be using cached analysis; ensure you pushed to the correct branch
4. **Stale workflow** - The workflow may be using an old version; check workflow file on main/dev

### State File Persists Across Sessions

**Symptoms:** Running `/fix-pr-reviews --loop` on the same PR skips issues you expected it to retry.

**Causes & Solutions:**
1. **By design** - Issue history persists across invocations on the same PR. Previously skipped issues remain skipped.
2. **To retry all issues** - Use `--reset` flag: `/fix-pr-reviews --loop --reset`
3. **To retry specific issues** - Manually edit `.claude-pr-fix-state.json` and remove entries from `issue_history`

### Context Exhausted Quickly

**Symptoms:** Loop mode stops after 1-2 iterations due to context limits.

**Causes & Solutions:**
1. **Auto-compaction not configured** - Ensure `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40` is set
2. **Large files** - Use `--critical-only` to reduce scope
3. **Many issues** - Consider running standard mode first to fix critical issues
4. **Complex codebase** - Break into smaller PRs with fewer changes

**Recovery:** If loop stops unexpectedly, state is preserved in `.claude-pr-fix-state.json`. Run `/fix-pr-reviews --continue` to resume.

### Workflow Timeout

**Symptoms:** Review workflow takes longer than 15 minutes.

**Causes & Solutions:**
1. **Large PR** - Set `LOOP_WAIT_TIMEOUT=30` for longer reviews
2. **Slow CI** - Check if other jobs are queued ahead
3. **Network issues** - Verify GitHub Actions status page

### Git Staging Conflicts

**Symptoms:** Unexpected files appear in `git diff` during validation.

**Causes & Solutions:**
1. **Auto-generated files** - Add to `.gitignore` or restore: `git restore <file>`
2. **IDE artifacts** - Close IDE or add exclusions to `.gitignore`
3. **Pre-commit hooks** - Hooks may modify files; review changes carefully

### State File Corruption

**Symptoms:** Loop behaves unexpectedly, skips issues, or shows wrong iteration count.

**Causes & Solutions:**
1. **Corrupted JSON** - Delete `.claude-pr-fix-state.json` and start fresh
2. **Stale state** - If PR changed significantly, delete state file to re-analyze
3. **Wrong PR** - State file is PR-specific; delete if switching PRs

**Reset command:**
```bash
/fix-pr-reviews --loop --reset
```

### Fixes Making Code Worse

**Symptoms:** Issue count increases after fixes, or lint/type errors appear.

**Causes & Solutions:**
1. **No pre-commit validation** - Ensure `npm run lint && npm run check:types` runs before commit
2. **Misunderstood issue** - Claude may misinterpret the reviewer's intent
3. **Complex refactoring** - Some issues need human judgment, not automated fixes

**Recovery:**
```bash
# Revert last commit if it made things worse
git revert HEAD

# Or revert specific file
git checkout HEAD~1 -- src/problematic-file.ts
```

The state file tracks `issue_history` — issues with 3+ failed attempts are automatically skipped. Use `--reset` to clear history and retry all issues.

---

## Error Recovery Reference

Quick reference for all error states and their recovery procedures.

### State File Errors

| Error | Detection | Recovery |
|-------|-----------|----------|
| **Corrupted JSON** | `jq empty` fails | Auto-backup to `.corrupted`, start fresh |
| **Wrong PR number** | `pr_number` != current branch PR | Auto-backup to `.prNNN`, start fresh |
| **Missing fields** | `jq -r '.field // empty'` returns empty | Use defaults, continue with partial state |
| **Stale state** | PR was force-pushed or rebased | Delete state file, re-fetch review |

### Git/Commit Errors

| Error | Detection | Recovery |
|-------|-----------|----------|
| **Lint failure** | `npm run lint` exits non-zero | Revert file, mark issue [SKIP-MANUAL], continue |
| **Type check failure** | `npm run check:types` exits non-zero | Revert file, mark issue [SKIP-MANUAL], continue |
| **Commit failure** | `git commit` exits non-zero | Check for empty staging, hooks failure; fix and retry |
| **Push failure** | `git push` exits non-zero | Check for conflicts, auth issues; manual intervention |
| **Staging mismatch** | Tracked files != `git diff --name-only` | Review discrepancy, discard or include |

### Workflow/CI Errors

| Error | Detection | Recovery |
|-------|-----------|----------|
| **Workflow not found** | `gh workflow list` doesn't include workflow | Use `--workflow=<correct-name>` |
| **Workflow timeout** | `gh run watch` exceeds timeout | Extend timeout or check manually |
| **Workflow failed** | `conclusion == "failure"` | Check logs, may need manual fix |
| **No new review** | No comment newer than push time | Assume clean or extend wait |

### Loop Control Errors

| Error | Detection | Recovery |
|-------|-----------|----------|
| **Max iterations** | `iteration >= max_iterations` | Stop, save state for `--continue` |
| **Regression** | `current_count > previous_count` | Revert last iteration, blacklist file |
| **Recurring issue** | `issue_history[key].attempts.length >= 3` or `status == "skipped"` | Auto-skip, notify user |
| **Auto-compaction mid-fix** | Context compaction occurs | Re-read state file, resume from saved state |

### Recovery Commands Cheat Sheet

```bash
# Reset issue history and start fresh
/fix-pr-reviews --loop --reset

# Resume from saved state (preserves iteration count)
/fix-pr-reviews --continue

# New loop session (resets iteration, preserves issue history)
/fix-pr-reviews --loop

# Revert uncommitted change (before commit - restores to last committed state)
git checkout HEAD -- src/path/to/file.ts

# Revert committed regression (after commit - restores to previous commit)
git checkout HEAD~1 -- src/path/to/file.ts

# Revert all uncommitted changes (WARNING: discards ALL iteration progress!)
# Only use this for explicit user-initiated abort when all fixes need to be discarded
git checkout -- .

# Revert last commit entirely (if already committed and pushed)
git revert HEAD

# Check workflow status
gh run list --workflow=claude-code-review.yml --limit=5

# Open PR in browser to inspect
gh pr view --web
```
