"""Tests for hooks/ollama-workers-status.py, the SessionStart announcement.

Run: python ollama-workers/tests/test_status_hook.py

OLLAMA_WORKERS_HOME holds the state file and a copy of this package's wrapper,
so the probe the hook runs reads test state and logs nowhere real.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOOK = os.path.join(PKG, "hooks", "ollama-workers-status.py")


class StatusHook(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ow-status-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "scripts"))
        shutil.copy(os.path.join(PKG, "scripts", "ollama-worker.ps1"), os.path.join(self.home, "scripts"))
        with open(os.path.join(self.home, "ollama-settings.json"), "w") as fh:
            fh.write("{}")

    def announce(self, **state):
        with open(os.path.join(self.home, "ollama-workers.json"), "w", encoding="utf-8") as fh:
            json.dump(dict({"enabled": True, "model": "glm-5.3-flash:cloud", "maxTurns": 25}, **state), fh)
        p = subprocess.run([sys.executable, HOOK], input=json.dumps({"cwd": self.home}).encode("utf-8"),
                           capture_output=True, env=dict(os.environ, OLLAMA_WORKERS_HOME=self.home), timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8")

    def test_worker_is_the_default_implementer(self):
        out = self.announce()
        self.assertIn("by default", out)
        self.assertNotIn("short-turn mechanical", out)

    def test_enforced_routing_is_announced_with_the_tag(self):
        out = self.announce(enforceRouting=True)
        self.assertIn("ENFORCED", out)
        self.assertIn("ROUTING-EXCEPTION: <reason>", out)

    def test_unenforced_routing_is_not_announced_as_enforced(self):
        out = self.announce(enforceRouting=False)
        self.assertNotIn("ENFORCED", out)

    def test_disabled_says_nothing(self):
        self.assertEqual(self.announce(enabled=False, enforceRouting=True).strip(), "")

    def test_probe_reads_the_state_under_ollama_workers_home(self):
        # The announced model comes from the test state, not ~/.claude.
        self.assertIn("model kimi-k3:cloud", self.announce(model="kimi-k3:cloud"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
