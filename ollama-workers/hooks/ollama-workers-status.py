"""SessionStart hook: announce the ollama worker routing rule, but only when it
is switched on. Disabled is the common case and costs no context.

The announcement carries the dispatchability of this session's cwd, because
`enabled` alone was never enough to act on. The worker only accepts a linked
git worktree, so in a primary checkout every dispatch is refused - and an
orchestrator that knows the rule and routes around it produces no error and no
log line at all. Announcing ON there, with no caveat and an instruction to
dispatch, is what made that silence reachable. The probe is the wrapper's own
preflight (`-Probe`), so this message cannot drift from what a dispatch does.
"""
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
STATE_PATH = os.path.join(HOME, ".claude", "ollama-workers.json")
WRAPPER = os.path.join(HOME, ".claude", "scripts", "ollama-worker.ps1")

ROUTING = (
    "dispatch short-turn mechanical implementer tasks to the ollama-worker agent "
    "instead of an Anthropic implementer; keep every reviewer, the plan-document "
    "reviewer, and fix rounds 4-5 on Anthropic (opus for reviews). Read the "
    "ollama-workers skill for the dispatch contract, the routing rubric, and the "
    "escalation ladder before the first dispatch."
)

FALLBACK = (
    "If you cannot dispatch into a worktree, route worker-shaped tasks to the "
    "Anthropic tier superpowers:subagent-driven-development Model Selection "
    "prescribes for them and say so, rather than leaving the routing unstated."
)


def probe(cwd):
    """Return the wrapper's probe verdict, or a string explaining why not.

    Fail-soft on purpose: a hook that raises loses the whole announcement, and
    an unannounced worker is the failure this file exists to prevent. Every
    error path still leaves the caller with the ON message.
    """
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return "pwsh not on PATH"
    if not os.path.exists(WRAPPER):
        return "wrapper not installed at %s" % WRAPPER
    try:
        out = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", WRAPPER, "-Probe", "-Cwd", cwd],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return "probe timed out"
    except OSError as exc:
        return "probe could not run (%s)" % exc

    line = next((l for l in out.stdout.splitlines() if l.lstrip().startswith("{")), None)
    if not line:
        return "probe returned no verdict"
    try:
        verdict = json.loads(line)
    except ValueError:
        return "probe verdict was not JSON"
    if "dispatchable" not in verdict:
        return "probe verdict had no dispatchable field"
    return verdict


try:
    with open(STATE_PATH, encoding="utf-8") as fh:
        state = json.load(fh)
except FileNotFoundError:
    sys.exit(0)
except (OSError, ValueError) as exc:
    print(f"ollama-workers: state file unreadable ({exc}); workers treated as OFF.")
    sys.exit(0)

if not state.get("enabled"):
    sys.exit(0)

model = state.get("model", "glm-5.3-flash:cloud")
max_turns = state.get("maxTurns", 25)

try:
    payload = json.load(sys.stdin)
except (OSError, ValueError):
    payload = {}
cwd = payload.get("cwd") or os.getcwd()

header = f"Ollama workers are ON (model {model}, maxTurns {max_turns})."
result = probe(cwd)

if isinstance(result, str):
    print(
        f"{header} Dispatchability of this directory could not be checked "
        f"({result}) - treat the first dispatch as the check. When executing a "
        f"superpowers plan, {ROUTING}"
    )
elif result.get("dispatchable"):
    print(f"{header} When executing a superpowers plan, {ROUTING}")
else:
    # Named as a step to take, not a dead end: the orchestrator dispatches into
    # a worktree it creates, so "primary checkout" is a precondition it can
    # satisfy. Telling it not to dispatch would trade one silent skip for
    # another. The worktree sentence is conditional because the other reasons
    # (a missing overlay, no ollama binary, an unusable model tag) are not
    # fixed by changing directory.
    reason = result.get("reason") or "unknown"
    remedy = " ".join((result.get("remedy") or "").split())
    parts = [
        f"{header} This directory is NOT DISPATCHABLE: {reason}.",
        f"A dispatch with -Cwd {cwd} would be refused, so workers being on "
        f"changes nothing here until that is fixed.",
    ]
    if reason in ("primary checkout", "not a git worktree"):
        parts.append(
            "The worker accepts only a linked git worktree. Before the first "
            "dispatch, make one (superpowers:using-git-worktrees) and pass that "
            "path as -Cwd."
        )
    if remedy:
        parts.append(remedy.rstrip(".") + ".")
    parts.append(FALLBACK)
    parts.append(
        f"When executing a superpowers plan from a dispatchable worktree, {ROUTING}"
    )
    print(" ".join(parts))
