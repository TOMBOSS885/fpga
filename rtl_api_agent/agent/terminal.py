"""Interactive terminal dashboard for the Verilog/SystemVerilog agent.

The competition entry points remain intentionally non-interactive.  This module
is a developer-facing shell around the same ``run.sh`` workflow: it edits a
small environment profile, diagnoses Vivado, and tails the agent trace while a
task is running.
"""
from __future__ import annotations

import argparse
import curses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import canonical_url
from .vivado import discover_vivado_bin, runtime_env

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run.sh"
CONFIG_VERSION = 1

# Keep this list deliberately small and reviewable.  Secrets are inherited
# from the process environment and are never written to a profile.
FIELD_DEFAULTS: dict[str, str] = {
    "LLM_BASE_URL": "http://127.0.0.1:8000/v1",
    "LLM_MODEL": "",
    "LLM_CONTEXT_TOKENS": "16384",
    "LLM_MAX_TOKENS": "4096",
    "LLM_TIMEOUT_S": "85",
    "LLM_STREAM": "0",
    "AGENT_DEADLINE_S": "360",
    "AGENT_MAX_REPAIRS": "3",
    "AGENT_TB_REPAIRS": "1",
    "AGENT_RESERVE_S": "8",
    "AGENT_LINT_S": "40",
    "AGENT_SIM_S": "45",
    "AGENT_SYNTH_S": "90",
    "RTL_PART": "xczu3eg-sbva484-1-e",
    "RTL_PERIOD_NS": "5",
    "VIVADO_BIN": "",
}

FIELD_LABELS = {
    "INPUT_DIR": "task input directory",
    "OUTPUT_DIR": "result output directory",
    "LLM_BASE_URL": "model endpoint",
    "LLM_MODEL": "model id",
    "LLM_CONTEXT_TOKENS": "context tokens",
    "LLM_MAX_TOKENS": "agent output tokens",
    "LLM_TIMEOUT_S": "model timeout (s)",
    "LLM_STREAM": "stream replies (0/1)",
    "AGENT_DEADLINE_S": "task deadline (s)",
    "AGENT_MAX_REPAIRS": "DUT repair attempts",
    "AGENT_TB_REPAIRS": "testbench repair attempts",
    "AGENT_RESERVE_S": "reserved time (s)",
    "AGENT_LINT_S": "compile budget (s)",
    "AGENT_SIM_S": "simulation budget (s)",
    "AGENT_SYNTH_S": "synthesis budget (s)",
    "RTL_PART": "target part",
    "RTL_PERIOD_NS": "clock period (ns)",
    "VIVADO_BIN": "Vivado bin directory",
}


def load_profile(path: Path) -> dict:
    """Load a profile without allowing arbitrary values to leak into env."""
    values = dict(FIELD_DEFAULTS)
    values["LLM_MODEL"] = os.environ.get("MODEL_NAME", "")
    values["LLM_BASE_URL"] = os.environ.get("OPENAI_BASE_URL", values["LLM_BASE_URL"])
    values.update({key: os.environ[key] for key in FIELD_DEFAULTS if key in os.environ})
    input_dir = ""
    output_dir = ""
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw.get("env", {}) if isinstance(raw, dict) else {}
            if isinstance(stored, dict):
                values.update({key: str(stored[key]) for key in FIELD_DEFAULTS if key in stored})
            if isinstance(raw, dict):
                input_dir = str(raw.get("input_dir", ""))
                output_dir = str(raw.get("output_dir", ""))
        except (OSError, ValueError, TypeError):
            pass
    return {"env": values, "input_dir": input_dir, "output_dir": output_dir}


def save_profile(path: Path, profile: dict) -> None:
    # Reject embedded credentials before writing a user-edited endpoint.
    canonical_url(profile["env"].get("LLM_BASE_URL", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CONFIG_VERSION,
        "track": "rtl",
        "language": "SystemVerilog/Verilog",
        "env": {key: str(profile.get("env", {}).get(key, "")) for key in FIELD_DEFAULTS},
        "input_dir": str(profile.get("input_dir", "")),
        "output_dir": str(profile.get("output_dir", "")),
    }
    temp = path.with_name(path.name + ".pending")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def apply_assignments(profile: dict, assignments: list[str]) -> None:
    values = profile["env"]
    known = set(FIELD_DEFAULTS) | {"INPUT_DIR", "OUTPUT_DIR"}
    for assignment in assignments:
        if "=" not in assignment:
            raise ValueError(f"--set expects KEY=VALUE: {assignment}")
        key, value = assignment.split("=", 1)
        key = key.strip().upper()
        if key not in known:
            raise ValueError(f"unsupported terminal setting: {key}")
        if key == "INPUT_DIR":
            profile["input_dir"] = value
        elif key == "OUTPUT_DIR":
            profile["output_dir"] = value
        else:
            values[key] = value


def tool_path(name: str, env: dict[str, str]) -> str | None:
    root = env.get("VIVADO_BIN", "").strip()
    if not root and env.get("XILINX_VIVADO"):
        root = str(Path(env["XILINX_VIVADO"]) / "bin")
    search = root or env.get("PATH", "")
    return shutil.which(name, path=search)


def launch_environment(profile: dict) -> dict[str, str]:
    current = dict(os.environ)
    for key in FIELD_DEFAULTS:
        value = profile["env"].get(key, "").strip()
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    # The profile edits the model once, for both agent and baseline aliases.
    if current.get("LLM_MODEL"):
        current["MODEL_NAME"] = current["LLM_MODEL"]
    else:
        current.pop("MODEL_NAME", None)
    return current


def task_paths(profile: dict) -> tuple[Path, Path]:
    paths = [profile.get(key, "").strip() for key in ("input_dir", "output_dir")]
    if not all(paths):
        raise ValueError("Set INPUT_DIR and OUTPUT_DIR (or pass --input/--output).")
    return tuple(Path(path).expanduser().resolve() for path in paths)


def doctor_report(env: dict[str, str] | None = None) -> dict:
    """Return an honest local status report; no installation is attempted."""
    current = dict(os.environ if env is None else env)
    discovered = discover_vivado_bin(current)
    if discovered:
        current["VIVADO_BIN"] = discovered
    current = runtime_env(current, Path(discovered) if discovered else None)
    binaries = {name: tool_path(name, current) for name in ("xvlog", "xelab", "xsim", "vivado")}
    running: list[str] = []
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,comm="], capture_output=True, text=True,
            timeout=2, check=False,
        ).stdout
        for line in listing.splitlines():
            if any(token in line.lower() for token in ("vivado", "xvlog", "xelab", "xsim", "vitis", "hw_server", "xsdb")):
                running.append(line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    version = ""
    if binaries["vivado"]:
        try:
            with tempfile.TemporaryDirectory(prefix="rtl_doctor_") as directory:
                probe = subprocess.run(
                    [binaries["vivado"], "-version"], capture_output=True, text=True,
                    timeout=6, check=False, env=current, cwd=directory,
                )
            version = (probe.stdout + "\n" + probe.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            version = "unreadable"
    xsim_version = ""
    xsim_error = ""
    if binaries["xsim"]:
        try:
            with tempfile.TemporaryDirectory(prefix="rtl_doctor_") as directory:
                probe = subprocess.run([binaries["xsim"], "-version"], capture_output=True, text=True,
                                       timeout=8, check=False, env=current, cwd=directory)
            combined = (probe.stdout + "\n" + probe.stderr).strip()
            xsim_version = next((line for line in combined.splitlines()
                                 if "xsim v" in line.lower() or "vivado simulator v" in line.lower()), "")
            if probe.returncode != 0:
                xsim_error = next((line for line in combined.splitlines() if "error" in line.lower()), combined[-240:])
        except (OSError, subprocess.SubprocessError, IndexError):
            xsim_error = "xsim probe failed"
    return {
        "binaries": binaries,
        "missing": [name for name, value in binaries.items() if not value],
        "running": running,
        "vivado_version": version,
        "xilinx_vivado": current.get("XILINX_VIVADO", ""),
        "vivado_bin": current.get("VIVADO_BIN", ""),
        "xsim_version": xsim_version,
        "xsim_error": xsim_error,
    }


def format_doctor(report: dict) -> list[str]:
    available = report["binaries"]
    lines = ["Vivado doctor (read-only)"]
    lines.append("tools: FOUND" if not report["missing"] else "tools: MISSING")
    lines.append("license / actual simulation / synthesis: not verified by this version probe")
    if report.get("vivado_version"):
        lines.append("version: " + report["vivado_version"])
    if report.get("xsim_version"):
        lines.append("xsim: " + report["xsim_version"])
    elif report.get("xsim_error"):
        lines.append("xsim probe: " + report["xsim_error"])
    for name in ("vivado", "xvlog", "xelab", "xsim"):
        lines.append(f"{name:7} {available[name] or 'missing'}")
    lines.append("running: " + ("; ".join(report["running"][-3:]) if report["running"] else "none"))
    return lines


def event_text(event: dict) -> str:
    tool = str(event.get("tool", "event"))
    phase = str(event.get("phase", event.get("event", "")))
    if tool == "llm":
        return f"llm {event.get('role', phase)} {event.get('event', '')}".strip()
    if tool in {"xvlog", "xelab", "xsim", "vivado"}:
        detail = event.get("diagnostic_code", "")
        status = event.get("event", "")
        return f"{tool} {phase} {status}" + (f" rc={event['rc']} {detail}" if "rc" in event else "")
    if tool == "checkpoint":
        return f"checkpoint L{event.get('internal_level', '?')} synth={event.get('synthesis_passed', False)}"
    if tool == "workflow" and event.get("event") == "finished":
        return f"finished L{event.get('internal_level', 0)} reason={event.get('reason', '')}"
    if tool == "workflow" and event.get("event") == "stage":
        return f"stage {phase} budget={event.get('remaining_s', '?')}s"
    if tool == "workflow" and event.get("event") == "checked":
        return f"{phase}: {event.get('kind', '')} {event.get('diagnostic_code', '')}".strip()
    if tool == "skill_selector":
        return "skills: " + ", ".join(event.get("selected", []))
    if tool == "agent" and event.get("event") == "error":
        return "agent error: " + str(event.get("error", ""))
    return " ".join(str(event.get(key)) for key in ("tool", "event", "kind") if event.get(key) is not None)


@dataclass
class TraceTail:
    path: Path
    offset: int = 0
    nonce: str | None = None
    identity: tuple | None = None
    header: bytes = b""
    active: bool = False

    def read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        events: list[dict] = []
        try:
            with self.path.open("rb") as fh:
                stat = os.fstat(fh.fileno())
                header = fh.readline()
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.offset or header != self.header:
                    self.offset = 0
                    self.active = self.nonce is None
                self.identity, self.header = identity, header
                fh.seek(self.offset)
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        break
                    self.offset = fh.tell()
                    try:
                        value = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(value, dict):
                        if value.get("tool") == "run_meta" and self.nonce is not None:
                            self.active = value.get("nonce") == self.nonce
                        if self.active:
                            events.append(value)
        except OSError:
            pass
        return events


@dataclass
class RunState:
    phase: str = "idle"
    tool: str = ""
    remaining_s: float | None = None
    updated_at: float = 0.0
    level: int = 0
    diagnostic: str = ""
    risks: str = ""
    skills: str = ""
    reason: str = ""

    def consume(self, event: dict) -> None:
        tool = event.get("tool")
        if tool == "workflow" and event.get("event") == "start":
            self.risks = ", ".join(event.get("profile_data", {}).get("tags", []))
        if tool == "workflow" and event.get("event") == "stage":
            self.phase = event.get("phase", "")
            self.tool = ""
            self.remaining_s = event.get("remaining_s")
            self.updated_at = time.monotonic()
        if tool in {"xvlog", "xelab", "xsim", "vivado"}:
            self.tool = str(tool) + " " + str(event.get("status", event.get("event", "")))
        if tool == "skill_selector":
            self.skills = ", ".join(event.get("selected", []))
        if tool == "workflow" and event.get("event") == "checked" and event.get("kind") != "ok":
            self.diagnostic = f"{event.get('category') or event.get('kind')}: {event.get('diagnostic_code', '')}"
        if tool == "checkpoint":
            self.level = event.get("internal_level", 0)
        if tool == "workflow" and event.get("event") == "finished":
            if self.remaining_s is not None:
                self.remaining_s = max(0, self.remaining_s - (time.monotonic() - self.updated_at))
            self.reason = event.get("reason", "finished")
            self.level = event.get("internal_level", 0)
            self.phase = "finished"

    def budget_text(self) -> str:
        if self.remaining_s is None:
            return "--"
        elapsed = time.monotonic() - self.updated_at if self.phase != "finished" else 0
        return f"{max(0, self.remaining_s - elapsed):.1f}s"


class Dashboard:
    def __init__(self, stdscr, profile: dict, config_path: Path, auto_run: bool = False):
        self.stdscr = stdscr
        self.profile = profile
        self.config_path = config_path
        self.rows = ["INPUT_DIR", "OUTPUT_DIR", *FIELD_DEFAULTS]
        self.selected = 0
        self.message = "Press r to run, d for Vivado status, e to edit, s to save, q to quit"
        self.logs: list[str] = []
        self.process: subprocess.Popen | None = None
        self.tail: TraceTail | None = None
        self.state = RunState()
        self.viewport_start = 0
        self.auto_run = auto_run
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    def draw_line(self, y: int, text: str, width: int, attr=0, x: int = 0) -> None:
        height, screen_width = self.stdscr.getmaxyx()
        count = min(width, screen_width - x - 1)
        if y < 0 or y >= height or x < 0 or count <= 0:
            return
        try:
            self.stdscr.addnstr(y, x, text.ljust(count)[:count], count, attr)
        except curses.error:
            # The terminal can resize between getmaxyx() and addnstr().
            pass

    def draw(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        title = "RTL AGENT | Verilog/SystemVerilog | AMD Vivado loop"
        self.draw_line(0, title, width, curses.color_pair(2) | curses.A_BOLD)
        self.draw_line(1, "config: " + str(self.config_path), width, curses.color_pair(3))
        self.draw_line(2, self.message, width, curses.color_pair(4))
        if height < 14 or width < 35:
            self.draw_line(height - 2, "Resize terminal to at least 35x14", width)
            self.draw_line(height - 1, "q quit", width)
            self.stdscr.refresh()
            return
        self.draw_line(3, f"stage: {self.state.phase} | {self.state.tool} | budget: {self.state.budget_text()}", width)
        self.draw_line(4, f"local L{self.state.level} (not official score) | last failure: {self.state.diagnostic or '--'}", width)
        self.draw_line(5, "RTL risks: " + (self.state.risks or "--"), width)
        self.draw_line(6, "skills: " + (self.state.skills or "--"), width)
        wide = width >= 100
        split = width // 2 if wide else width
        count = height - 10 if wide else max(2, (height - 11) // 2)
        self.viewport_start = min(self.viewport_start, self.selected)
        self.viewport_start = max(self.viewport_start, self.selected - count + 1)
        self.draw_line(8, f"SETTINGS {self.selected + 1}/{len(self.rows)} (scroll with arrows)", split - 1, curses.A_BOLD)
        for index in range(self.viewport_start, min(len(self.rows), self.viewport_start + count)):
            key = self.rows[index]
            marker = ">" if index == self.selected else " "
            if key in FIELD_DEFAULTS:
                value = self.profile["env"].get(key, "") or "(unset)"
            else:
                value = self.profile.get(key.lower(), "") or "(unset)"
            if key in {"LLM_MODEL", "VIVADO_BIN"} and len(value) > 32:
                value = "..." + value[-29:]
            self.draw_line(9 + index - self.viewport_start, f"{marker} {FIELD_LABELS[key]:24} {value}", split - 1,
                           curses.color_pair(1) if index == self.selected else 0)
        log_x, log_y = (split + 1, 8) if wide else (0, 9 + count)
        self.draw_line(log_y, "LIVE TRACE", width - log_x, curses.A_BOLD, x=log_x)
        log_count = max(0, height - log_y - 2)
        for index, line in enumerate(self.logs[-log_count:] if log_count else []):
            self.draw_line(log_y + 1 + index, line, width - log_x, x=log_x)
        footer = "UP/DOWN select  ENTER/e edit  s save  d doctor  r run  q quit"
        self.draw_line(height - 1, footer, width, curses.color_pair(3))
        self.stdscr.refresh()

    def edit(self) -> None:
        key = self.rows[self.selected]
        height, width = self.stdscr.getmaxyx()
        if height < 3 or width < 4:
            return
        self.stdscr.nodelay(False)
        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        self.draw_line(height - 3, f"Edit {key} (blank clears, Esc cancels)", width)
        self.draw_line(height - 2, "", width)
        try:
            raw = self.stdscr.getstr(height - 2, 0, width - 1).decode("utf-8", "replace")
            if "\x1b" in raw:
                return
            if key in FIELD_DEFAULTS:
                self.profile["env"][key] = raw.strip()
            else:
                self.profile[key.lower()] = raw.strip()
            self.message = f"updated {key}; press s to persist"
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.stdscr.nodelay(True)

    def start(self) -> None:
        if self.process is not None:
            self.message = "agent is already running"
            return
        try:
            input_dir, output_dir = task_paths(self.profile)
            save_profile(self.config_path, self.profile)
            env = launch_environment(self.profile)
            env["RUN_NONCE"] = uuid.uuid4().hex
            output_dir.mkdir(parents=True, exist_ok=True)
            self.tail = TraceTail(output_dir / "trace.jsonl", nonce=env["RUN_NONCE"])
            with (output_dir / "console.log").open("w", encoding="utf-8") as log:
                self.process = subprocess.Popen([str(RUNNER), str(input_dir), str(output_dir)],
                                                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
            self.state, self.logs = RunState(phase="starting"), []
            self.message = "agent running; trace is live; runner output in console.log"
        except (OSError, ValueError) as exc:
            self.message = str(exc)

    def read_trace(self) -> None:
        if self.tail:
            for event in self.tail.read():
                self.state.consume(event)
                self.logs.append(event_text(event))
            self.logs = self.logs[-200:]

    def tick(self) -> None:
        self.read_trace()
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self.read_trace()
            self.process = None
            self.message = f"finished rc={code} L{self.state.level}: {self.state.reason or 'runner failed; see console.log'}"

    def loop(self) -> None:
        if self.auto_run:
            self.start()
            self.auto_run = False
        while True:
            self.tick()
            self.draw()
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1
            if key in (ord("q"), ord("Q")) and self.process is None:
                return
            if key in (curses.KEY_UP, ord("k")):
                self.selected = (self.selected - 1) % len(self.rows)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.selected = (self.selected + 1) % len(self.rows)
            elif key in (curses.KEY_ENTER, 10, 13, ord("e"), ord("E")):
                self.edit()
            elif key in (ord("s"), ord("S")):
                try:
                    save_profile(self.config_path, self.profile)
                    self.message = "profile saved"
                except (OSError, ValueError) as exc:
                    self.message = str(exc)
            elif key in (ord("d"), ord("D")):
                self.logs.extend(format_doctor(doctor_report(launch_environment(self.profile))))
                self.message = "Vivado status refreshed"
            elif key in (ord("r"), ord("R")):
                self.start()
            time.sleep(0.08)


def run_plain(profile: dict, config_path: Path) -> int:
    try:
        input_dir, output_dir = task_paths(profile)
        save_profile(config_path, profile)
        env = launch_environment(profile)
        env["RUN_NONCE"] = uuid.uuid4().hex
        output_dir.mkdir(parents=True, exist_ok=True)
        tail = TraceTail(output_dir / "trace.jsonl", nonce=env["RUN_NONCE"])
        process = subprocess.Popen([str(RUNNER), str(input_dir), str(output_dir)], cwd=str(ROOT), env=env)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    while process.poll() is None:
        for event in tail.read():
            print("[" + event_text(event) + "]", flush=True)
        time.sleep(0.1)
    for event in tail.read():
        print("[" + event_text(event) + "]", flush=True)
    return int(process.returncode or 0)


def run_dashboard(profile: dict, config_path: Path, auto_run: bool) -> int:
    def launch(stdscr):
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_MAGENTA, -1)
        except curses.error:
            pass
        Dashboard(stdscr, profile, config_path, auto_run).loop()
    curses.wrapper(launch)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Terminal dashboard for the RTL Verilog/SystemVerilog agent")
    parser.add_argument("--input", help="task directory containing prompt.txt")
    parser.add_argument("--output", help="directory for solution.v and trace.jsonl")
    parser.add_argument("--config", default=".rtl-agent.json", help="editable terminal profile")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a profile field")
    parser.add_argument("--doctor", action="store_true", help="print read-only Vivado/toolchain status")
    parser.add_argument("--no-ui", action="store_true", help="run with plain terminal output")
    parser.add_argument("--auto-run", action="store_true", help="start immediately when the dashboard opens")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    profile = load_profile(config_path)
    if args.input:
        profile["input_dir"] = args.input
    if args.output:
        profile["output_dir"] = args.output
    try:
        apply_assignments(profile, args.set)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.doctor:
        report = doctor_report(launch_environment(profile))
        print("\n".join(format_doctor(report)))
        return 0
    if args.no_ui or not (sys.stdin.isatty() and sys.stdout.isatty()):
        if not profile.get("input_dir") or not profile.get("output_dir"):
            print("Non-interactive mode requires --input and --output.", file=sys.stderr)
            return 2
        return run_plain(profile, config_path)
    return run_dashboard(profile, config_path, args.auto_run)


if __name__ == "__main__":
    raise SystemExit(main())
