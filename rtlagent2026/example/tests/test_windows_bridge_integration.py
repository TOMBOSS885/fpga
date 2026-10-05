"""Opt-in Windows bridge integration tests; no model or Vivado license required.

RUN_WINDOWS_BRIDGE_TESTS=1 python3 -m unittest discover -s tests -p test_windows_bridge_integration.py -v
"""

import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.windows_vivado import WindowsVivado, detect_root, windows_path


@unittest.skipUnless(os.environ.get("RUN_WINDOWS_BRIDGE_TESTS") == "1", "opt-in Windows/WSL integration")
class WindowsProcessTests(unittest.TestCase):
    def setUp(self):
        self.bridge = WindowsVivado(detect_root())
        self.work = Path(tempfile.mkdtemp(prefix="bridge test ", dir=self.bridge.scratch))
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_exit_code_and_space_in_path(self):
        script = self.work / "exit test.bat"
        script.write_text("@echo off\necho bridge-output\nexit /b 7\n", encoding="ascii")
        rc, log = self.bridge.run(script, [], self.work, 5)
        self.assertEqual(rc, 7, log)
        self.assertIn("bridge-output", log)

    def test_timeout_terminates_child(self):
        pid_path = self.work / "child.pid"
        child = f"$PID | Set-Content -LiteralPath '{windows_path(pid_path)}'; Start-Sleep -Seconds 60"
        encoded = base64.b64encode(child.encode("utf-16-le")).decode("ascii")
        script = self.work / "sleep.bat"
        script.write_text("@echo off\npowershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded + "\n", encoding="ascii")
        rc, log = self.bridge.run(script, [], self.work, 5)
        self.assertEqual(rc, 124, log)
        self.assertTrue(pid_path.is_file(), "Child was not started; timeout cleanup not verified")
        pid = int(pid_path.read_text(encoding="utf-8-sig").strip())
        query = f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 1 }} else {{ exit 0 }}"
        encoded_query = base64.b64encode(query.encode("utf-16-le")).decode("ascii")
        result = subprocess.run([self.bridge.powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_query],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, "Timed-out child is still running")


if __name__ == "__main__":
    unittest.main()
