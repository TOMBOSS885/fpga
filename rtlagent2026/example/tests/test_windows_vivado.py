import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import tools
from agent import windows_vivado as bridge


class BridgeTests(unittest.TestCase):
    def test_command_quotes_spaces(self):
        command = bridge.cmd_arguments(r"F:\EDA Tools\bin\xvlog.bat", ["--sv", "solution.sv"])
        self.assertEqual(command, '/d /s /c ""F:\\EDA Tools\\bin\\xvlog.bat" "--sv" "solution.sv""')

    def test_shell_injection_is_rejected(self):
        for value in ('bad&whoami', '%PATH%', 'bad"', 'bad\n', 'bad|more'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.cmd_arguments("F:\\xvlog.bat", [value])

    def test_wsl_conversion_uses_wslpath(self):
        with patch.object(bridge, "is_wsl", return_value=True), \
                patch.object(bridge.subprocess, "check_output", return_value="F:\\work\n") as convert:
            self.assertEqual(bridge.windows_path("/mnt/f/work"), "F:\\work")
            self.assertEqual(convert.call_args.args[0][:2], ["wslpath", "-w"])

    def test_unc_scratch_rejected(self):
        with patch.object(bridge, "is_wsl", return_value=True), \
                patch.object(bridge.subprocess, "check_output", return_value="\\\\wsl.localhost\\Ubuntu\\tmp\n"):
            with self.assertRaises(ValueError):
                bridge.windows_path("/tmp")

    def test_timeout_script_kills_only_owned_tree(self):
        self.assertIn("/PID $proc.Id /T /F", bridge._POWERSHELL)
        self.assertNotIn("/IM", bridge._POWERSHELL)
        self.assertIn("-WindowStyle Hidden", bridge._POWERSHELL)

    def test_root_detection_from_existing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "bin" / "vivado.bat").touch()
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(bridge.shutil, "which", return_value=str(root / "bin" / "vivado")):
                self.assertEqual(bridge.detect_root(), root)

    def test_windows_runner_returns_status_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            instance = bridge.WindowsVivado.__new__(bridge.WindowsVivado)
            instance.powershell = "powershell.exe"
            def fake_process(*args, **kwargs):
                (work / "test.result.json").write_text(json.dumps({"exit_code": 7, "timed_out": False, "error": ""}))
                (work / "test.stdout.log").write_text("syntax error")
                return subprocess.CompletedProcess(args, 0, b"")
            with patch.object(bridge.uuid, "uuid4") as token, \
                    patch.object(bridge, "windows_path", side_effect=lambda p: str(p)), \
                    patch.object(bridge.subprocess, "run", side_effect=fake_process):
                token.return_value.hex = "test"
                rc, log = instance.run("xvlog.bat", [], work, 2)
            self.assertEqual(rc, 7)
            self.assertIn("syntax error", log)


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.rtl = tools.RtlToolchain.__new__(tools.RtlToolchain)
        self.rtl.reason = ""
        self.rtl.bridge = None
        self.rtl.xvlog, self.rtl.xelab, self.rtl.vivado = "xvlog", "xelab", "vivado"

    def test_lint_spawn_failure_stays_environment_failure(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(-1, "cannot launch")) as execute:
            self.assertEqual(self.rtl.lint("module TopModule; endmodule", "TopModule")[0], -1)
            self.assertEqual(execute.call_count, 1)

    def test_lint_native_command_and_shared_budget(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(0, "OK")) as execute:
            self.assertEqual(self.rtl.lint("module TopModule; endmodule", "TopModule", 10)[0], 0)
            self.assertEqual(execute.call_args_list[0].args[0], ["xvlog", "--sv", "solution.sv"])
            self.assertLessEqual(execute.call_args_list[1].args[2], 10)

    def test_synth_requires_checkpoint_and_success_marker(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(0, "RTL_AGENT_SYNTH_OK")):
            self.assertEqual(self.rtl.synth("module TopModule; endmodule", "TopModule")[0], 1)
            (self.work / "synthesized.dcp").write_bytes(b"test checkpoint")
            self.assertEqual(self.rtl.synth("module TopModule; endmodule", "TopModule")[0], 0)

    def test_probe_rejects_nonworking_executable(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(1, "Could not find executable")):
            self.rtl._probe()
        self.assertFalse(self.rtl.available)

    def test_missing_part_is_an_environment_failure(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(2, "RTL_AGENT_ENV_ERROR: target part missing")):
            self.assertEqual(self.rtl.synth("module TopModule; endmodule", "TopModule")[0], -1)

    def test_probe_reads_version(self):
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(0, "Vivado v2022.2 (64-bit)")):
            self.rtl._probe()
        self.assertEqual(self.rtl.version, "2022.2")

    def test_top_does_not_allow_tcl_or_cmd_injection(self):
        self.assertEqual(self.rtl.synth("", "TopModule; exec whoami")[0], 1)

    def test_tcl_echo_is_not_a_diagnostic(self):
        log = '# puts "RTL_AGENT_ENV_ERROR: missing part"\nRTL_AGENT_SYNTH_OK\n'
        self.assertEqual(tools.summarize_log(log), "RTL_AGENT_SYNTH_OK")
        with patch.object(self.rtl, "_workdir", return_value=str(self.work)), \
                patch.object(self.rtl, "_cleanup"), \
                patch.object(self.rtl, "_execute", return_value=(0, log)):
            (self.work / "synthesized.dcp").write_bytes(b"test checkpoint")
            self.assertEqual(self.rtl.synth("module TopModule; endmodule", "TopModule")[0], 0)


if __name__ == "__main__":
    unittest.main()
