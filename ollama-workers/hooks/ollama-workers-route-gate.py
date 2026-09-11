"""PreToolUse hook (matcher: Agent): make the ollama worker the default implementer.

While workers are ON and `enforceRouting` is true in ~/.claude/ollama-workers.json,
an implementer dispatch to anything other than the ollama-worker agent is denied
unless its prompt carries a line `ROUTING-EXCEPTION: <reason>`. The deny reason
tells the orchestrator what to do, so the cost of a legitimate Anthropic
implementer is one re-dispatch with the tag, and every such exception is written
to the log with its reason.

Prose rules about routing have already failed silently once: an orchestrator
read them, routed around them, and nothing recorded it. This is the one place
the rule is checked rather than trusted.

Only implementer dispatches are gated. They are recognised by how their prompt
opens (SDD's implementer template and the hand-written variants seen in real
sessions: "You are implementing Task N", "Fix round 1 for Task N", "A prior
implementer attempted...") or, when the prompt only points at a dispatch file,
by a description such as "Implement Task 3: ...". Reviewers, explorers, forks
and everything else pass. It is a nudge with known gaps, not a proof: a prompt
that names neither its role nor an implementing description is not seen.

Fail open, always: any error exits 0 with no output, so the tool call proceeds.
A broken gate must cost the routing preference, never the pipeline.
`enforceRouting` is read on every call, so `/ollama-workers enforce off` stops
enforcement in every running session at once.
"""
import datetime
import json
import os
import re
import sys

HOME = os.environ.get("OLLAMA_WORKERS_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
STATE_PATH = os.path.join(HOME, "ollama-workers.json")
LOG_PATH = os.path.join(HOME, "ollama-workers.log.jsonl")

# Implementer prompts state their role in the first sentence. Looking only at
# the opening keeps a reviewer prompt that quotes an implementer's report, or a
# long prompt that mentions implementing somewhere, from being gated.
#
# "performing" counts only with a task after it: 61 real reviewer dispatches open
# "You are performing a scoped re-review" or "...the final code review".
HEAD_CHARS = 600
IMPLEMENTER_OPENING = re.compile(
    r"\byou are (implementing|finishing|resuming|the implementer|an implementer)\b"
    r"|\byou are performing (task \d+|one task|the task)\b"
    r"|^\W*implementation task\b"
    r"|\bfix round \d+ (for|of)\b"
    r"|\ba prior implementer\b",
    re.IGNORECASE,
)
REVIEWER_OPENING = re.compile(
    r"\byou are (re-?reviewing|reviewing|an? [\w -]*reviewer|performing [^.\n]{0,80}\breview)",
    re.IGNORECASE,
)
# For prompts that only say "Your full instructions are in this file": the
# description is then the one place the role is named.
IMPLEMENTER_DESCRIPTION = re.compile(r"^\s*(implement(er|ing)?|fix round|fix wave)\b|\bimplementer\b", re.IGNORECASE)
REVIEW_WORD = re.compile(r"review", re.IGNORECASE)
EXCEPTION_TAG = re.compile(r"^[ \t>*_-]*ROUTING-EXCEPTION:[ \t]*(\S[^\n]{3,})$", re.MULTILINE)

DENY_REASON = (
    "ollama-workers routing gate: this is an implementer dispatch to an Anthropic "
    "model, and ollama workers are ON with routing enforced, so implementers go to "
    "the ollama-worker agent by default. KEEP IT ON ANTHROPIC when the task is on "
    "the hazard list - auth/permissions, DB migrations, money movement or "
    "settlement, webhook signature verification - or needs tools the worker does "
    "not have (MCP servers such as Supabase, production access), needs design "
    "judgment, is a fix round 4-5 escalation, has already failed on the worker "
    "twice (killed or escalated), or there is no dispatchable linked worktree. "
    "To keep it on Anthropic, dispatch again with a line in the prompt: "
    "`ROUTING-EXCEPTION: <reason>` (for example `ROUTING-EXCEPTION: DB migration - "
    "hazard list`). Otherwise write the brief to a file and dispatch "
    "Agent(subagent_type: \"ollama-worker\", model: \"haiku\", prompt: "
    "\"-BriefFile <path> -Cwd <worktree> -Label <task-id>\") per the ollama-workers "
    "skill's Dispatch contract. The user can stop enforcement with "
    "/ollama-workers enforce off."
)


def load_state():
    with open(STATE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def log_row(row):
    line = json.dumps(row, ensure_ascii=False)
    # Dozens of sessions append here at once; a lost row is a lost routing decision.
    for _ in range(3):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return
        except OSError:
            continue


def is_implementer(prompt, description):
    head = prompt[:HEAD_CHARS]
    if REVIEWER_OPENING.search(head):
        return False
    if IMPLEMENTER_OPENING.search(head):
        return True
    return bool(IMPLEMENTER_DESCRIPTION.search(description)) and not REVIEW_WORD.search(description)


def main():
    # Claude Code writes UTF-8. Python's default stdin codec on Windows is the
    # locale's (cp1252), which raises on bytes such as a closing smart quote's -
    # and a raise here fails open.
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if payload.get("tool_name") not in ("Agent", "Task"):
        return None

    try:
        state = load_state()
    except (OSError, ValueError):
        return None
    if state.get("enabled") is not True or state.get("enforceRouting") is not True:
        return None

    tool_input = payload.get("tool_input") or {}
    if tool_input.get("subagent_type") == "ollama-worker":
        return None
    prompt = tool_input.get("prompt")
    description = tool_input.get("description")
    if not isinstance(prompt, str):
        return None
    if not isinstance(description, str):
        description = ""
    if not is_implementer(prompt, description):
        return None

    base = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": "gate",
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "agent_type": payload.get("agent_type"),
        "subagent_type": tool_input.get("subagent_type"),
        "model": tool_input.get("model"),
        "description": description[:120],
    }
    tag = EXCEPTION_TAG.search(prompt)
    if tag:
        log_row(dict(base, decision="exception", reason=tag.group(1).strip()[:200]))
        return None

    log_row(dict(base, decision="deny"))
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON,
        }
    }


if __name__ == "__main__":
    try:
        decision = main()
    except Exception:
        decision = None
    if decision:
        sys.stdout.write(json.dumps(decision))
    sys.exit(0)
