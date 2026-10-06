"""Toolchain interface and deterministic verification utilities for RTL Agent V1.

Provides:
- Interface verification and auto-patching
- xvlog + xelab single module linting (L1)
- xsim self-checking testbench simulation (L2 bridge)
- Vivado synth_design out-of-context synthesis (L3)
- Structured diagnostic log summarizer
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time

PART = os.environ.get("RTL_PART", "xczu3eg-sbva484-1-e")


def _scratch_dir() -> str | None:
    # Official runners expose EDA_TMP on node-local storage.  Keep the
    # project-specific override first for tests and parallel workers.
    d = os.environ.get("AGENT_SCRATCH") or os.environ.get("EDA_TMP")
    if d:
        os.makedirs(d, exist_ok=True)
        return d
    return None


def _run(cmd: list[str], cwd: str, timeout_s: float) -> tuple[int, str]:
    process_env = os.environ.copy()
    if os.name == "nt":
        process_env.setdefault("XILINX_LOCAL_USER_DATA", "NO")
        if cmd[0].lower().endswith((".bat", ".cmd")):
            command = subprocess.list2cmdline(cmd)
            cmd = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe")]) + ' /d /s /c "' + command + '"'
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
            text=True,
            errors="replace",
            env=process_env,
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        return 124, f"command timed out after {timeout_s:.1f}s: {' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 127, f"command not found: {exc}"


def _parse_port_items(decl: str) -> list[tuple[str, int, str]]:
    """Parse ANSI Verilog ports, including inherited directions.

    Models often emit ``input a, b`` or put ``logic``/``signed`` between the
    direction and the name.  The old parser only accepted one port per comma
    item, silently dropping the inherited names.  Keeping this parser shared
    by the spec and guard prevents the two contract checks from disagreeing.
    """
    decl = re.sub(r"//[^\n]*", "", decl)
    decl = re.sub(r"/\*.*?\*/", "", decl, flags=re.DOTALL)
    result: list[tuple[str, int, str]] = []
    direction: str | None = None
    width = 1
    for raw in decl.split(","):
        item = raw.strip()
        if not item:
            continue
        dm = re.search(r"\b(input|output|inout)\b", item, re.IGNORECASE)
        if dm:
            direction = dm.group(1).lower()
            item = item[dm.end():].strip()
            width = 1
        if direction is None:
            continue
        rm = re.search(r"\[\s*(-?\d+)\s*:\s*(-?\d+)\s*\]", item)
        if rm:
            width = abs(int(rm.group(1)) - int(rm.group(2))) + 1
            item = item[:rm.start()] + item[rm.end():]
        item = re.sub(r"\b(?:wire|reg|logic|signed|unsigned|var)\b", " ", item)
        # A declaration may contain a default value; the identifier precedes it.
        nm = re.search(r"\b([A-Za-z_]\w*)\b", item)
        if nm:
            result.append((direction, width, nm.group(1)))
    return result


class RtlToolchain:
    def __init__(self) -> None:
        # Auto-detect Vivado if not on PATH
        if not shutil.which("xvlog"):
            viv_cands = [
                os.environ.get("XILINX_VIVADO"),
                os.environ.get("VIVADO_PATH"),
                "/tools/Xilinx/2026.1/Vivado",
                "/tools/Xilinx/Vivado/2026.1",
                "/tools/Xilinx/Vivado/2024.2",
                "/opt/Xilinx/Vivado/2024.2",
            ]
            for cand in viv_cands:
                if cand and os.path.isdir(os.path.join(cand, "bin")):
                    bin_dir = os.path.join(cand, "bin")
                    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                    break

        bindir = os.environ.get("VIVADO_BIN", "")
        def locate(name):
            candidate = os.path.join(bindir, name + (".bat" if os.name == "nt" else ""))
            return candidate if bindir and os.path.isfile(candidate) else shutil.which(name)
        self.xvlog = locate("xvlog")
        self.xelab = locate("xelab")
        self.xsim = locate("xsim")
        self.vivado = locate("vivado")
        self.reason = ""

        missing = [n for n, p in (("xvlog", self.xvlog), ("xelab", self.xelab),
                                  ("xsim", self.xsim), ("vivado", self.vivado)) if not p]
        if missing:
            self.reason = f"not on PATH: {', '.join(missing)}; source your Vivado settings64.sh"

    @property
    def available(self) -> bool:
        return not self.reason

    @staticmethod
    def _license_error(log: str) -> bool:
        return bool(re.search(
            r"(?:license\s+checkout\s+failed|checkout\s+failed|no\s+valid\s+license|"
            r"valid\s+license\s+(?:was\s+)?not\s+found|"
            r"(?:failed|unable)\s+to\s+(?:obtain|checkout|check\s+out)[^\n]*license|"
            r"could\s+not\s+obtain[^\n]*license|license[^\n]*(?:expired|not\s+available)|"
            r"not\s+licensed|FLEX(?:lm|net)\s+Licensing\s+error)",
            log or "", re.IGNORECASE,
        ))

    # -------------------------------------------------------- zero-cost preflight
    def preflight(self, source: str, top: str) -> tuple[int, str]:
        """Reject obvious non-RTL artifacts before invoking Vivado.

        This is the deterministic left edge of the tool loop.  It implements
        the synthesis-latch skill's hard restrictions and catches a model that
        accidentally returned a testbench or an explanation wrapped around the
        module.  It deliberately does not try to prove functional correctness.
        """
        if not source or not source.strip():
            return 1, "PREFLIGHT: empty RTL source"
        clean = re.sub(r"//[^\n]*", "", source)
        clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
        if not re.search(rf"\bmodule\s+{re.escape(top)}\b", clean):
            return 1, f"PREFLIGHT: top module '{top}' was not found"
        if not re.search(r"\bendmodule\b", clean):
            return 1, "PREFLIGHT: missing endmodule"
        forbidden = [
            (r"\binitial\b", "initial block"),
            (r"\$finish\b", "$finish"),
            (r"\$display\b", "$display"),
            (r"\b(?:real|time)\b", "simulation-only scalar"),
            (r"#\s*\d", "delay control"),
            (r"\bfork\b|\bjoin\b", "fork/join"),
        ]
        for pattern, label in forbidden:
            if re.search(pattern, clean, re.IGNORECASE):
                return 1, f"PREFLIGHT: non-synthesizable {label} in DUT"
        return 0, "PREFLIGHT: synthesizable source shape accepted"

    # ------------------------------------------------------------- L1: lint
    def lint(self, source: str, top: str, timeout_s: float = 120.0) -> tuple[int, str]:
        """Verify DUT parses and elaborates without testbench."""
        rc_pre, log_pre = self.preflight(source, top)
        if rc_pre != 0:
            return rc_pre, log_pre
        if not self.available:
            return -1, f"vivado unavailable: {self.reason}"

        work = tempfile.mkdtemp(prefix="agent_lint_", dir=_scratch_dir())
        deadline = time.monotonic() + timeout_s
        try:
            src = os.path.join(work, "solution.sv")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(source)

            rc, out = _run([self.xvlog or "xvlog", "--sv", "solution.sv"], work, timeout_s)
            if self._license_error(out):
                return -2, "LICENSE_ERROR: compilation license unavailable\n" + summarize_log(out)
            if rc != 0:
                return 1, summarize_log(out)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 1, "LINT_TIMEOUT: compilation consumed the stage budget"
            rc, out2 = _run(
                [self.xelab or "xelab", top, "-s", "dut_snapshot", "-timescale", "1ps/1ps"],
                work, remaining,
            )
            if self._license_error(out2):
                return -2, "LICENSE_ERROR: elaboration license unavailable\n" + summarize_log(out2)
            return (0 if rc == 0 else 1), summarize_log(out + "\n" + out2)
        finally:
            if os.environ.get("AGENT_KEEP_WORK") != "1":
                shutil.rmtree(work, ignore_errors=True)

    # ---------------------------------------------------- L2: self-simulation
    def run_sim(self, dut_source: str, tb_source: str, tb_top: str = "tb_self_check",
                timeout_s: float = 180.0) -> tuple[int, str]:
        """Run self-checking simulation on DUT + synthesized Testbench."""
        if not self.available:
            return -1, f"vivado unavailable: {self.reason}"

        work = tempfile.mkdtemp(prefix="agent_sim_", dir=_scratch_dir())
        try:
            with open(os.path.join(work, "dut.sv"), "w", encoding="utf-8") as fh:
                fh.write(dut_source)
            with open(os.path.join(work, "tb.sv"), "w", encoding="utf-8") as fh:
                fh.write(tb_source)

            # 1. Compile both
            rc, out1 = _run([self.xvlog or "xvlog", "--sv", "dut.sv", "tb.sv"], work, timeout_s / 3)
            if self._license_error(out1):
                return -2, "LICENSE_ERROR: compilation license unavailable\n" + summarize_log(out1)
            if rc != 0:
                return 1, "TB compilation failed:\n" + summarize_log(out1)

            # 2. Elaborate
            rc, out2 = _run(
                [self.xelab or "xelab", tb_top, "-s", "sim_snap", "-timescale", "1ps/1ps"],
                work, timeout_s / 3,
            )
            if self._license_error(out2):
                return -2, "LICENSE_ERROR: elaboration license unavailable\n" + summarize_log(out2)
            if rc != 0:
                return 1, "TB elaboration failed:\n" + summarize_log(out1 + "\n" + out2)

            # xelab creates the snapshot; xsim is the executable simulator.
            rc, out3 = _run([self.xsim or "xsim", "sim_snap", "-runall"], work, timeout_s / 3)
            full_log = out1 + "\n" + out2 + "\n" + out3

            if self._license_error(full_log):
                return -2, "LICENSE_ERROR: simulator license unavailable\n" + summarize_log(full_log)

            # Check simulator output, rather than xelab's informational log.
            if rc != 0:
                return 1, "SIM_PROCESS_FAILURE: nonzero simulator exit\n" + summarize_log(full_log)
            if re.search(r"TB_FAILURE|ASSERTION\s+FAILED|(?:^|\s)(?:ERROR|FATAL):", out3, re.IGNORECASE):
                return 1, "Self-checking testbench reported mismatch:\n" + summarize_log(out3)
            verdicts = re.findall(r"^\s*TB_RESULT\s+checks=(\d+)\s+errors=(\d+)\s*$", out3, re.MULTILINE)
            if len(verdicts) != 1:
                # Vivado/xsim can exit successfully after failing to obtain a
                # simulator license, while emitting neither a mismatch nor a
                # verdict line.  Treat the missing verdict as an environment
                # failure so the repair loop does not corrupt a valid DUT.
                return -3, "VERIFICATION_UNAVAILABLE: missing or ambiguous structured TB_RESULT\n" + summarize_log(full_log)
            checks, errors = map(int, verdicts[0])
            if checks <= 0:
                return -3, "VERIFICATION_UNAVAILABLE: testbench performed zero reported comparisons"
            if errors:
                return 1, f"Self-checking testbench reported {errors} mismatches in {checks} comparisons."
            return 0, f"Self-checking testbench passed {checks} reported comparisons."
        finally:
            if os.environ.get("AGENT_KEEP_WORK") != "1":
                shutil.rmtree(work, ignore_errors=True)

    # ----------------------------------------------------------- L3: synth
    def synth(self, source: str, top: str, timeout_s: float = 240.0) -> tuple[int, str]:
        """Run out-of-context synthesis targeting ZU3EG."""
        if not self.available:
            return -1, f"vivado unavailable: {self.reason}"

        work = tempfile.mkdtemp(prefix="agent_synth_", dir=_scratch_dir())
        try:
            with open(os.path.join(work, "solution.sv"), "w", encoding="utf-8") as fh:
                fh.write(source)
            with open(os.path.join(work, "synth.tcl"), "w", encoding="utf-8") as fh:
                fh.write(
                    textwrap.dedent(
                        f"""\
                        read_verilog -sv solution.sv
                        synth_design -top {top} -part {PART} -mode out_of_context
                        puts "AGENT_SYNTH_SUCCESS"
                        """
                    )
                )
            rc, out = _run(
                [self.vivado or "vivado", "-mode", "batch", "-source", "synth.tcl",
                 "-nojournal", "-log", "synth.log"],
                work, timeout_s,
            )
            if self._license_error(out):
                return -2, "LICENSE_ERROR: synthesis license unavailable\n" + summarize_log(out)
            completed = any(marker in out for marker in (
                "Synthesis finished", "Finished Technology Mapping",
                "synth_design completed", "Synthesis completed", "AGENT_SYNTH_SUCCESS",
            ))
            if rc == 0 and not completed:
                # A missing synthesis marker with rc=0 is the characteristic
                # silent-license/tool-environment failure documented for the
                # official Vivado image, not evidence that the RTL is wrong.
                return -2, "ENVIRONMENT_ERROR: Vivado exited 0 without AGENT_SYNTH_SUCCESS:\n" + summarize_log(out)
            ok = (rc == 0 and completed
                  and not re.search(r"(?:^|\s)ERROR:", out, re.IGNORECASE)
                  and "Synthesis failed" not in out)
            return (0 if ok else 1), summarize_log(out)
        finally:
            if os.environ.get("AGENT_KEEP_WORK") != "1":
                shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------- Interface contract checking

def ports_from_prompt(prompt: str) -> tuple[str, list[tuple[str, int, str]]]:
    """Extract top module name and port list (dir, width, name) from prompt text with high resilience."""
    text = prompt or ""

    # 1. Try finding an explicit module declaration first
    m_mod = re.search(r"module\s+([A-Za-z_]\w*)\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;", text, re.DOTALL)
    if m_mod:
        top_name = m_mod.group(1)
        ports = _parse_ports_from_header(m_mod.group(2))
        if ports:
            return top_name, ports

    # 2. Extract top module name from prompt text
    m_top = re.search(r"module\s+(?:named\s+)?([A-Za-z_]\w*)", text)
    top_name = m_top.group(1) if m_top else "TopModule"
    if top_name.lower() in ("with", "the", "named", "implement", "for", "a"):
        top_name = "TopModule"

    ports: list[tuple[str, int, str]] = []
    seen = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Pattern A: Bullet with direction first: - input [7:0] in / - input in (8 bits) / - input clk
        m_bullet = re.search(
            r"^[*\-]?\s*(input|output|inout)\s+(?:reg\s+|wire\s+|logic\s+)?(?:signed\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?([a-zA-Z_]\w*)\s*(?:\(\s*(\d+)\s*(?:bits?|位)\s*\))?",
            line,
            re.IGNORECASE,
        )
        if m_bullet:
            direction = m_bullet.group(1).lower()
            msb, lsb = m_bullet.group(2), m_bullet.group(3)
            name = m_bullet.group(4)
            paren_bits = m_bullet.group(5)
            if msb is not None and lsb is not None:
                width = abs(int(msb) - int(lsb)) + 1
            elif paren_bits is not None:
                width = int(paren_bits)
            else:
                width = 1
            if name not in seen:
                seen.add(name)
                ports.append((direction, width, name))
            continue

        # Pattern B: Colon format: - in: input, 8 bits or - clk: input (1 bit)
        m_colon = re.search(
            r"^[*\-]?\s*([a-zA-Z_]\w*)\s*:\s*(input|output|inout)(?:[,\s]+(\d+)\s*(?:bits?|位))?",
            line,
            re.IGNORECASE,
        )
        if m_colon:
            name = m_colon.group(1)
            direction = m_colon.group(2).lower()
            bits = m_colon.group(3)
            width = int(bits) if bits else 1
            if name not in seen:
                seen.add(name)
                ports.append((direction, width, name))

        # Common prose form: ``input [7:0] data`` without a bullet.
        if not m_bullet and not m_colon:
            m_plain = re.search(
                r"^\s*(?:[-*]\s*)?(input|output|inout)\s+(?:wire\s+|reg\s+|logic\s+)?"
                r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?([A-Za-z_]\w*)"
                r"(?:\s*\(\s*(\d+)\s*(?:bits?|位)\s*\))?",
                line, re.IGNORECASE,
            )
            if m_plain:
                direction = m_plain.group(1).lower()
                msb, lsb, name, bits = m_plain.groups()[1:]
                width = abs(int(msb) - int(lsb)) + 1 if msb and lsb else int(bits or 1)
                if name not in seen:
                    seen.add(name)
                    ports.append((direction, width, name))

    return top_name, ports


def _parse_ports_from_header(decl: str) -> list[tuple[str, int, str]]:
    """Parse ports from Verilog module header."""
    return _parse_port_items(decl)


def parse_interface_contract(interface: str, prompt: str = "") -> tuple[str, list[tuple[str, int, str]], bool]:
    """Return ``(top, ports, authoritative)`` for the task contract.

    ``interface.txt`` is authoritative when it contains a parseable module or
    port list.  A prompt-derived list is advisory because natural-language
    problem statements occasionally contain a typo in a port description.
    """
    raw = interface or ""
    if raw.strip():
        mm = re.search(r"module\s+([A-Za-z_]\w*)\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;", raw, re.DOTALL | re.IGNORECASE)
        if mm:
            ports = _parse_ports_from_header(mm.group(2))
            if ports:
                return mm.group(1), ports, True
        top, ports = ports_from_prompt(raw)
        if ports:
            return top, ports, True
    top, ports = ports_from_prompt(prompt)
    return top, ports, False


def check_and_patch_interface(source: str, interface: str, prompt: str = "") -> tuple[int, str, str]:
    """Check module interface contract and perform non-destructive header patching if needed.

    Returns: (rc, diagnosis_message, patched_or_original_source)
    """
    want_name = None
    want_ports = []

    want_name, want_ports, _ = parse_interface_contract(interface, prompt)

    if not want_name:
        want_name = "TopModule"

    m_got = re.search(r"module\s+([A-Za-z_]\w*)\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;", source, re.DOTALL)
    if not m_got:
        return 1, "ERROR: No module declaration found in generated Verilog code", source

    got_name = m_got.group(1)
    got_ports = _parse_ports_from_header(m_got.group(2))

    problems = []
    if got_name != want_name:
        problems.append(f"Module name mismatch: got '{got_name}', required '{want_name}'")

    want_map = {n: (d, w) for d, w, n in want_ports}
    got_map = {n: (d, w) for d, w, n in got_ports}

    for name in want_map:
        if name not in got_map:
            problems.append(f"Missing required port '{name}'")
    for name in got_map:
        if name not in want_map:
            problems.append(f"Extra undeclared port '{name}'")

    for name in want_map.keys() & got_map.keys():
        wd, ww = want_map[name]
        gd, gw = got_map[name]
        if wd != gd:
            problems.append(f"Port '{name}' direction mismatch: required {wd}, got {gd}")
        if ww != gw:
            problems.append(f"Port '{name}' width mismatch: required {ww}-bit, got {gw}-bit")

    # If minor naming or header issue, attempt in-place surgical header repair
    patched = source
    if problems and want_ports:
        # Construct compliant standard header
        port_lines = []
        for d, w, n in want_ports:
            w_str = f"[{w-1}:0] " if w > 1 else ""
            # if output, determine if reg is needed by grepping assignments in body
            is_output = (d == "output")
            needs_reg = is_output and bool(re.search(rf"\b{n}\s*(?:<=|\+=|-=|=)(?!=)", source))
            type_str = "reg " if needs_reg else ""
            port_lines.append(f"  {d} {type_str}{w_str}{n}")
        new_header = f"module {want_name} (\n" + ",\n".join(port_lines) + "\n);"

        # Replace existing header
        patched = re.sub(
            r"module\s+[A-Za-z_]\w*\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;",
            new_header,
            source,
            count=1,
            flags=re.DOTALL
        )
        return (0 if len(problems) <= 2 else 1), "Header auto-aligned to interface contract: " + "; ".join(problems), patched

    if problems:
        return 1, "Interface Contract Violation: " + "; ".join(problems), source

    return 0, "Interface contract fully satisfied.", source


def summarize_log(log: str, max_lines: int = 35) -> str:
    """Categorized summary filter for Vivado and Simulation diagnostic logs."""
    if not log:
        return ""

    lines = log.splitlines()
    critical_lines = []
    error_patterns = [
        re.compile(r"ERROR:\s*\[.*?\]"),
        re.compile(r"CRITICAL WARNING:\s*\[.*?\]"),
        re.compile(r"syntax error", re.IGNORECASE),
        re.compile(r"TB_FAILURE"),
        re.compile(r"ASSERTION FAILED"),
        re.compile(r"Mismatches:"),
        re.compile(r"cannot find port", re.IGNORECASE),
        re.compile(r"inferring latch", re.IGNORECASE),
        re.compile(r"multi-driven net", re.IGNORECASE),
    ]

    for line in lines:
        if any(pat.search(line) for pat in error_patterns):
            critical_lines.append(line.strip())

    if critical_lines:
        return "\n".join(critical_lines[:max_lines])

    # Fallback to tail of log
    return "\n".join(lines[-max_lines:])
