"""SessionStart hook: announce the ollama worker routing rule, but only when it
is switched on. Disabled is the common case and costs no context."""
import json
import os
import sys

state_path = os.path.join(os.path.expanduser("~"), ".claude", "ollama-workers.json")

try:
    with open(state_path, encoding="utf-8") as fh:
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

print(
    f"Ollama workers are ON (model {model}, maxTurns {max_turns}). When executing a "
    "superpowers plan, dispatch short-turn mechanical implementer tasks to the "
    "ollama-worker agent instead of an Anthropic implementer; keep every reviewer, "
    "the plan-document reviewer, and fix rounds 4-5 on Anthropic (opus for reviews). "
    "Read the ollama-workers skill for the dispatch contract, the routing rubric, "
    "and the escalation ladder before the first dispatch."
)
