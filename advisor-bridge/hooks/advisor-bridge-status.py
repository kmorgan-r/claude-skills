"""SessionStart hook: teach a non-Anthropic-backed session that it can still
reach an Anthropic advisor, but only when the bridge is switched on.

Two gates, and neither substitutes for the other. The config gate comes FIRST:
base-URL alone would inject the protocol into every Ollama session while the
bridge is off, spending context on every session and steering the model into
calls that exit 1 - so turning the bridge off would not turn its surface off.
The base-URL gate keeps the protocol out of sessions that already have the
native advisor tool.

Silent on every config branch (absent, unreadable, malformed, disabled), which
is a deliberate divergence from ollama-workers-status.py:79 - that hook prints
on its unreadable branch, but it also checks nothing before doing so. This one
runs its config read before the base-URL check, so a print there would land in
every Anthropic session too.
"""
import json
import os
import sys
from urllib.parse import urlparse

HOME = os.environ.get("ADVISOR_BRIDGE_HOME") or os.path.join(
    os.path.expanduser("~"), ".claude"
)
CONFIG_PATH = os.path.join(HOME, "advisor-bridge.json")

PROTOCOL = """You have an advisor: a stronger reviewer that sees this session's full
transcript. This session's backend is not Anthropic, so the built-in advisor tool is
unavailable; the bridge below reaches one anyway, in a separate process.

Call it BEFORE substantive work - before writing, before committing to an
interpretation, before building on an assumption. Orientation (finding files, reading
what is there) is not substantive work; writing, editing and declaring an answer are.
Also call it when stuck, when considering a change of approach, and when you believe
the task is complete - making the deliverable durable first.

    Bash(command: "pwsh -NoProfile -File ~/.claude/scripts/advisor-bridge.ps1",
         timeout: 300000)

The timeout is not optional. A real transcript takes well over the 120s default, and
without it you see a killed call instead of advice.

Weigh the answer: primary-source evidence in your own transcript outranks the advice.
If they genuinely conflict, make one reconciling call rather than switching silently."""


def main():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        sys.exit(0)

    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        sys.exit(0)

    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        sys.exit(0)
    try:
        host = urlparse(base).hostname or ""
    except ValueError:
        sys.exit(0)
    if host == "api.anthropic.com":
        sys.exit(0)

    print(PROTOCOL)


main()
