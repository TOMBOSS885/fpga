import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.terminal import (
    Dashboard,
    FIELD_DEFAULTS,
    RunState,
    TraceTail,
    apply_assignments,
    doctor_report,
    discover_vivado_bin,
    event_text,
    format_doctor,
    launch_environment,
    load_profile,
    save_profile,
    task_paths,
)


class TerminalProfileTests(unittest.TestCase):
    def test_profile_round_trip_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            with patch.dict(os.environ, {"LLM_MODEL": "from-env", "LLM_API_KEY": "private"}, clear=False):
                profile = load_profile(path)
            apply_assignments(profile, ["LLM_MODEL=local-model", "INPUT_DIR=/tmp/task", "OUTPUT_DIR=/tmp/out"])
            save_profile(path, profile)
            raw = json.loads(path.read_text())
            self.assertEqual(raw["env"]["LLM_MODEL"], "local-model")
            self.assertNotIn("LLM_API_KEY", json.dumps(raw))
            loaded = load_profile(path)
            self.assertEqual(loaded["input_dir"], "/tmp/task")
            self.assertEqual(loaded["output_dir"], "/tmp/out")

    def test_unknown_setting_is_rejected(self):
        profile = {"env": dict(FIELD_DEFAULTS), "input_dir": "", "output_dir": ""}
        with self.assertRaises(ValueError):
            apply_assignments(profile, ["DANGEROUS=value"])

    def test_profile_model_alias_and_cleared_path_apply_to_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"MODEL_NAME": "inherited", "VIVADO_BIN": "/old/bin"}, clear=True):
            profile = load_profile(Path(directory) / "absent.json")
            self.assertEqual(profile["env"]["LLM_MODEL"], "inherited")
            apply_assignments(profile, ["LLM_MODEL=selected", "VIVADO_BIN="])
            env = launch_environment(profile)
            self.assertEqual(env["LLM_MODEL"], env["MODEL_NAME"])
            self.assertNotIn("VIVADO_BIN", env)

    def test_credential_url_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            profile = load_profile(path)
            apply_assignments(profile, ["LLM_BASE_URL=http://user:secret@localhost/v1"])
            with self.assertRaises(ValueError):
                save_profile(path, profile)
            self.assertFalse(path.exists())

    def test_relative_task_paths_resolve_before_runner_changes_directory(self):
        input_dir, output_dir = task_paths({"input_dir": "tasks/example", "output_dir": "~/rtl-result"})
        self.assertEqual(input_dir, Path.cwd() / "tasks/example")
        self.assertEqual(output_dir, Path.home() / "rtl-result")

    def test_doctor_uses_configured_vivado_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("xvlog", "xelab", "xsim", "vivado"):
                target = root / name
                target.write_text("#!/bin/sh\n")
                target.chmod(0o755)
            report = doctor_report({"PATH": "", "VIVADO_BIN": str(root)})
            self.assertEqual(report["missing"], [])
            self.assertFalse(report["running"])
            self.assertNotIn("READY", "\n".join(format_doctor(report)))
            self.assertIn("not verified", "\n".join(format_doctor(report)))

    def test_doctor_discovers_home_vivado_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "vivado26.1" / "2026.1" / "Vivado" / "bin"
            root.mkdir(parents=True)
            (root / "vivado").write_text("#!/bin/sh\n")
            (root / "vivado").chmod(0o755)
            with patch("agent.terminal.Path.home", return_value=Path(directory)):
                self.assertEqual(discover_vivado_bin({}), str(root))

    def test_explicit_environment_and_path_precede_local_fallback(self):
        self.assertEqual(discover_vivado_bin({"VIVADO_BIN": "/selected/bin", "XILINX_VIVADO": "/other"}), "/selected/bin")
        self.assertEqual(discover_vivado_bin({"XILINX_VIVADO": "/tools/Xilinx/2026.1/Vivado"}), "/tools/Xilinx/2026.1/Vivado/bin")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "vivado").write_text("#!/bin/sh\n")
            (Path(directory) / "vivado").chmod(0o755)
            self.assertEqual(discover_vivado_bin({"PATH": directory}), directory)
            report = doctor_report({"PATH": directory, "VIVADO_BIN": "/missing/selected/bin"})
            self.assertIn("vivado", report["missing"])

    def test_trace_event_text_is_concise(self):
        self.assertEqual(event_text({"tool": "checkpoint", "internal_level": 2, "synthesis_passed": False}),
                         "checkpoint L2 synth=False")
        self.assertIn("xsim", event_text({"tool": "xsim", "phase": "simulation", "rc": 0}))
        started = event_text({"tool": "xsim", "phase": "simulation", "event": "started"})
        self.assertIn("started", started)
        self.assertNotIn("rc=", started)


class TraceAndDashboardTests(unittest.TestCase):
    def test_tail_partial_lines_replacement_and_rerun_nonce(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text('{"tool":"run_meta","nonce":"old"}\n{"tool":"workflow","event":"finished"}\n')
            tail = TraceTail(path, nonce="new")
            self.assertEqual(tail.read(), [])
            # Same inode, new run; the new file may grow beyond the old offset.
            path.write_text('{"tool":"run_meta","nonce":"new"}\n' + '{"tool":"xvlog","event":"started","padding":"' + "x" * 100 + '"}\n{"tool":"xsim"')
            self.assertEqual([e["tool"] for e in tail.read()], ["run_meta", "xvlog"])
            self.assertEqual(tail.read(), [])
            with path.open("a") as stream:
                stream.write('}\n')
            self.assertEqual(tail.read(), [{"tool": "xsim"}])
            replacement = path.with_suffix(".new")
            replacement.write_text('{"tool":"run_meta","nonce":"new"}\n')
            replacement.replace(path)
            self.assertEqual(tail.read(), [{"tool": "run_meta", "nonce": "new"}])

    def test_structured_rtl_progress_is_visible(self):
        state = RunState()
        for event in [
            {"tool": "workflow", "event": "start", "profile_data": {"tags": ["fsm", "async_reset"]}},
            {"tool": "workflow", "event": "stage", "phase": "simulation", "remaining_s": 40},
            {"tool": "xsim", "event": "started", "status": "running"},
            {"tool": "workflow", "event": "checked", "kind": "environment", "category": "environment", "diagnostic_code": "license"},
            {"tool": "checkpoint", "internal_level": 1},
        ]:
            state.consume(event)
        self.assertEqual(state.phase, "simulation")
        self.assertEqual(state.tool, "xsim running")
        self.assertIn("license", state.diagnostic)
        self.assertIn("async_reset", state.risks)
        self.assertLessEqual(float(state.budget_text()[:-1]), 40)
        self.assertEqual(state.level, 1)

    def test_resizing_and_last_setting_edit_stay_in_bounds(self):
        class Screen:
            def __init__(self, height, width):
                self.size, self.lines = (height, width), []
            def getmaxyx(self): return self.size
            def nodelay(self, value): pass
            def keypad(self, value): pass
            def erase(self): self.lines.clear()
            def refresh(self): pass
            def addnstr(self, y, x, text, n, attr=0):
                assert 0 <= y < self.size[0] and 0 <= x < self.size[1]
                assert n > 0 and x + n < self.size[1]
                self.lines.append((y, x, text))
            def getstr(self, y, x, n):
                assert 0 <= y < self.size[0] and 0 <= x < self.size[1] and x + n < self.size[1]
                return b"/selected/vivado/bin"

        with patch("agent.terminal.curses.curs_set"), patch("agent.terminal.curses.color_pair", return_value=0), \
                patch("agent.terminal.curses.echo"), patch("agent.terminal.curses.noecho"):
            for height, width in [(24, 80), (30, 120), (14, 40), (8, 20)]:
                with self.subTest(size=(height, width)):
                    screen = Screen(height, width)
                    profile = {"env": dict(FIELD_DEFAULTS)}
                    dashboard = Dashboard(screen, profile, Path("/tmp/profile.json"))
                    dashboard.selected = len(dashboard.rows) - 1
                    dashboard.logs = ["xsim simulation started"] * 50
                    dashboard.draw()
                    if height >= 14:
                        self.assertTrue(any("Vivado bin" in line for _, _, line in screen.lines))
                        self.assertTrue(any("LIVE TRACE" in line for _, _, line in screen.lines))
                    dashboard.edit()
                    self.assertEqual(profile["env"]["VIVADO_BIN"], "/selected/vivado/bin")


if __name__ == "__main__":
    unittest.main()
