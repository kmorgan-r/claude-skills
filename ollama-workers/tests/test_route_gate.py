"""Table tests for hooks/ollama-workers-route-gate.py.

Run: python ollama-workers/tests/test_route_gate.py

Each case runs the hook as a subprocess, exactly as Claude Code does, with a
throwaway OLLAMA_WORKERS_HOME so no case reads the real state file or writes to
the real ~/.claude/ollama-workers.log.jsonl.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# ROUTE_GATE_HOOK points the suite at another copy of the hook - a mutant, to
# check that a case actually fails when the behaviour it guards is removed.
HOOK = os.environ.get("ROUTE_GATE_HOOK") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "ollama-workers-route-gate.py")

IMPLEMENTER = "You are implementing Task 3: Emit the migrations.\n\n## Task Description\n\nRead your task brief first: C:/x/task-3.md"
REVIEWER = "You are reviewing one task's implementation: first whether it matches its spec, then its quality. The implementer claims..."
RE_REVIEW = "You are re-reviewing one task's fix round. A previous review produced findings; the implementer says..."
FIX_ROUND = "Fix round 1 for Task 11 of the advisor-bridge plan. Worktree `C:/x` - run everything from there."
ESCALATED = "A prior implementer attempted Task 2 twice and stalled both times; you own it now. Work in C:/x."
FIX_IMPLEMENTER = "You are the implementer for fix round 1 of Task 6 in slice 4."
EXPLORE = "Find every caller of match_documents and report file:line."


class RouteGate(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ow-gate-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.log = os.path.join(self.home, "ollama-workers.log.jsonl")

    def state(self, **kw):
        s = {"enabled": True, "model": "glm-5.3-flash:cloud", "maxTurns": 25, "enforceRouting": True}
        s.update(kw)
        with open(os.path.join(self.home, "ollama-workers.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh)

    def run_hook(self, tool_input=None, tool_name="Agent", raw=None):
        # ensure_ascii=False: Claude Code serialises with JSON.stringify, which
        # sends non-ASCII as raw UTF-8, not \u escapes. Escaped input is pure ASCII
        # and would never exercise the stdin codec.
        payload = raw if raw is not None else json.dumps(
            {"session_id": "s1", "cwd": "C:/repo", "hook_event_name": "PreToolUse",
             "tool_name": tool_name, "tool_input": tool_input or {}}, ensure_ascii=False)
        # The child's stdin codec is forced to cp1252, Windows' default for a
        # pipe. A machine with PYTHONIOENCODING=utf-8 set would otherwise hide a
        # hook that trusts the default codec.
        env = dict(os.environ, OLLAMA_WORKERS_HOME=self.home, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        # Bytes, UTF-8: that is what Claude Code writes to a hook's stdin. text=True
        # would encode with the locale codec (cp1252 on Windows) and hide the
        # decode bug the em-dash case below exists to catch.
        p = subprocess.run([sys.executable, HOOK], input=payload.encode("utf-8"),
                           capture_output=True, env=env, timeout=20)
        stderr = p.stderr.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, "hook must always exit 0 (fail open); stderr=%s" % stderr)
        out = p.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def log_rows(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def assertDenied(self, decision):
        self.assertIsNotNone(decision, "expected a deny decision, got allow")
        hso = decision["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        return hso["permissionDecisionReason"]

    # --- switched off: never interferes -------------------------------------
    def test_no_state_file_allows(self):
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))

    def test_workers_disabled_allows(self):
        self.state(enabled=False)
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))

    def test_enforce_off_allows(self):
        self.state(enforceRouting=False)
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))

    def test_enforce_missing_allows(self):
        self.state()
        with open(os.path.join(self.home, "ollama-workers.json"), "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "model": "glm-5.3-flash:cloud"}, fh)
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))

    # --- the rule --------------------------------------------------------------
    def test_implementer_to_anthropic_is_denied(self):
        self.state()
        reason = self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "model": "sonnet", "prompt": IMPLEMENTER}))
        self.assertIn("ROUTING-EXCEPTION:", reason)
        self.assertIn("ollama-worker", reason)

    def test_deny_message_leads_with_hazard_list(self):
        self.state()
        reason = self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))
        self.assertLess(reason.index("hazard"), reason.index("ROUTING-EXCEPTION:"),
                        "the hazard-list instruction must come before the tag syntax")
        for word in ("migration", "auth"):
            self.assertIn(word, reason.lower())

    def test_default_subagent_type_is_still_checked(self):
        self.state()
        self.assertDenied(self.run_hook({"prompt": IMPLEMENTER}))

    def test_ollama_worker_dispatch_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "ollama-worker", "model": "haiku",
                                         "prompt": "-BriefFile C:/b.md -Cwd C:/wt -Label task-3"}))

    def test_exception_tag_allows_and_is_logged(self):
        self.state()
        prompt = IMPLEMENTER + "\n\nROUTING-EXCEPTION: DB migration (hazard list)"
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt, "description": "Task 3"}))
        rows = [r for r in self.log_rows() if r.get("event") == "gate"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "exception")
        self.assertEqual(rows[0]["reason"], "DB migration (hazard list)")

    def test_deny_is_logged(self):
        self.state()
        self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER, "description": "Implement Task 3"})
        rows = [r for r in self.log_rows() if r.get("event") == "gate"]
        self.assertEqual([r["decision"] for r in rows], ["deny"])
        self.assertEqual(rows[0]["description"], "Implement Task 3")

    def test_empty_exception_reason_is_denied(self):
        self.state()
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER + "\nROUTING-EXCEPTION:   "}))

    def test_non_ascii_prompt_is_still_checked(self):
        # A cp1252 stdin decode fails open on this prompt, letting it through the
        # gate. The closing smart quote is the character that proves it: its
        # UTF-8 bytes end in 0x9D, which cp1252 cannot decode, whereas an em dash
        # decodes (wrongly) without raising and would pass either way.
        self.state()
        prompt = "You are implementing Task 2 \u2014 the \u201ccaf\u00e9\u201d roster.\n\nRead your task brief first: C:/x.md"
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}))

    def test_performing_a_task_is_an_implementer(self):
        self.state()
        prompt = "You are performing Task 6 \u2014 the last task of a 7-task plan. It re-baselines a gate."
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "model": "opus", "prompt": prompt}))

    def test_performing_a_review_allows(self):
        # 61 real reviewer dispatches open this way.
        self.state()
        for prompt in ("You are performing a **scoped re-review** of a fix diff. Verdict each open finding.",
                       "You are performing the final whole-branch code review before this branch opens a PR.",
                       "You are performing a **design and accessibility review** of new UI."):
            self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}), prompt)

    def test_implementing_a_review_fix_round_is_an_implementer(self):
        # The word "review" later in an implementer's opening must not exempt it.
        self.state()
        prompt = ("You are implementing the ONE fix round that follows the final whole-branch "
                  "review of CR slice 2, PR 2 (M4: the Initial Review freeze).")
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}))

    def test_fix_round_is_an_implementer(self):
        self.state()
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": FIX_ROUND}))

    def test_escalated_owner_is_an_implementer(self):
        self.state()
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": ESCALATED}))

    def test_fix_implementer_is_an_implementer(self):
        self.state()
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": FIX_IMPLEMENTER}))

    # Real dispatches that hand the whole task over in a file: the opening says
    # nothing, so the description is the signal.
    FILE_POINTER = ("Your full instructions are in this file. Read it first and follow it exactly: "
                    "C:/repo/.superpowers/sdd/2026-09-11-plan/task-1-dispatch.md")

    def test_file_pointer_with_implement_description_is_an_implementer(self):
        self.state()
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "model": "haiku",
                                         "description": "Implement Task 1: processQuantity helper",
                                         "prompt": self.FILE_POINTER}))

    def test_file_pointer_with_review_description_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "model": "opus",
                                         "description": "Review Task 1", "prompt": self.FILE_POINTER}))

    def test_fix_re_review_description_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "description": "Task 7 fix re-review",
                                         "prompt": self.FILE_POINTER}))

    def test_implementer_subagent_opening_is_an_implementer(self):
        self.state()
        prompt = ("You are an implementer subagent. Your complete instructions - task, context, hard rules, "
                  "and report contract - are in this file. Read it first: C:/x/task-3.md")
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}))

    def test_implementation_task_opening_is_an_implementer(self):
        self.state()
        prompt = "IMPLEMENTATION TASK. Work ONLY in the git worktree `C:/x`, which is checked out on branch `feat/y`."
        self.assertDenied(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}))

    def test_fork_gathering_context_allows(self):
        self.state()
        prompt = ("You're continuing the /ship pipeline's P4 implementation phase, running "
                  "subagent-driven-development on docs/superpowers/plans/x.md. I've already read the plan.")
        self.assertIsNone(self.run_hook({"subagent_type": "fork", "description": "Finish plan read + preflight scan + ledger",
                                         "prompt": prompt}))

    # --- not implementers --------------------------------------------------------
    def test_reviewer_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "model": "opus", "prompt": REVIEWER}))

    def test_re_reviewer_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "model": "opus", "prompt": RE_REVIEW}))

    def test_explore_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "Explore", "prompt": EXPLORE}))

    def test_marker_far_into_prompt_allows(self):
        self.state()
        prompt = "Summarise this transcript for me.\n" + ("x" * 2000) + "\nYou are implementing Task 1"
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": prompt}))

    def test_other_tools_allow(self):
        self.state()
        self.assertIsNone(self.run_hook({"command": "echo You are implementing Task 1"}, tool_name="Bash"))

    # --- fail open -------------------------------------------------------------
    def test_malformed_stdin_allows(self):
        self.state()
        self.assertIsNone(self.run_hook(raw="{not json"))

    def test_malformed_state_allows(self):
        with open(os.path.join(self.home, "ollama-workers.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": IMPLEMENTER}))

    def test_non_string_prompt_allows(self):
        self.state()
        self.assertIsNone(self.run_hook({"subagent_type": "general-purpose", "prompt": ["not", "a", "string"]}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
