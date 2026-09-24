"""Repo-hygiene guards: each one pins a bug this project actually shipped."""
import json
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "server")


class TestRequirements(unittest.TestCase):
    def test_lines_are_unquoted_specs(self):
        # v0.2.0 shipped `"mcp<2"` — quotes are literal in requirements files
        # and pip refuses to parse them.
        with open(os.path.join(SERVER, "requirements.txt"), encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.assertTrue(lines)
        for line in lines:
            self.assertNotIn('"', line, f"quoted requirement breaks pip: {line}")
            self.assertNotIn("'", line, f"quoted requirement breaks pip: {line}")
            self.assertRegex(line, r"^[A-Za-z0-9][A-Za-z0-9._\[\]<>!=,~;+-]*$")

    def test_laya_pin_is_current(self):
        with open(os.path.join(SERVER, "requirements.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("laya==0.3.20", text)


class TestMetadataConsistency(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "plugin.json"), encoding="utf-8") as f:
            self.plugin = json.load(f)
        with open(os.path.join(REPO, "package.json"), encoding="utf-8") as f:
            self.package = json.load(f)

    def test_license_is_one_value_everywhere(self):
        # v0.2.0 shipped plugin.json=Apache-2.0 vs package/LICENSE=MIT.
        self.assertEqual(self.plugin["license"], self.package["license"])
        self.assertEqual(self.plugin["license"], "MIT")
        with open(os.path.join(REPO, "LICENSE"), encoding="utf-8") as f:
            self.assertIn("MIT License", f.read(64))

    def test_versions_match(self):
        # v0.2.0 shipped plugin.json=0.2.0 vs package.json=0.1.0.
        self.assertEqual(self.plugin["version"], self.package["version"])
        self.assertRegex(self.plugin["version"], r"^\d+\.\d+\.\d+$")

    def test_changelog_lists_current_version(self):
        with open(os.path.join(REPO, "CHANGELOG.md"), encoding="utf-8") as f:
            changelog = f.read()
        self.assertIn(f"[{self.plugin['version']}]", changelog)


class TestMcpManifest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, ".mcp.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.server = self.manifest["mcpServers"]["laya-judge"]

    def test_no_forced_checkpoint_pin(self):
        # Auto-routing (non-Latin -> multilingual) is the documented contract;
        # v0.2.0 pinned LAYA_MODEL=english here and silently disabled it.
        self.assertNotIn("LAYA_MODEL", self.server.get("env", {}))

    def test_timeout_covers_cold_checkpoint_download(self):
        # First start downloads both checkpoints (~0.7GB); v0.2.0's 120s could
        # not cover it.
        self.assertGreaterEqual(self.server.get("timeout", 0), 300_000)


class TestSidecarDocstring(unittest.TestCase):
    def test_invocation_matches_actual_filename(self):
        # v0.2.0 told users to run `python server/http.py`; the file is sidecar.py.
        path = os.path.join(SERVER, "sidecar.py")
        with open(path, encoding="utf-8") as f:
            first = f.read(400)
        self.assertIn("sidecar.py", first)
        self.assertNotIn("server/http.py", first)


class TestCoreStaysDependencyFree(unittest.TestCase):
    def test_import_does_not_pull_torch_or_laya(self):
        code = (
            "import sys; sys.path.insert(0, r'%s'); import core;"
            "bad = [m for m in ('torch', 'laya', 'mcp') if m in sys.modules];"
            "print(','.join(bad))" % SERVER
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
