"""Windows Vivado subprocess bridge for native Python and WSL development."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import uuid


def is_wsl():
    return os.name != "nt" and "microsoft" in platform.release().lower()


def local_path(value):
    if is_wsl() and re.match(r"^[A-Za-z]:[\\/]", value):
        return subprocess.check_output(["wslpath", "-u", value], text=True, timeout=5).strip()
    return value


def windows_path(value):
    path = str(Path(value).resolve())
    if is_wsl():
        path = subprocess.check_output(["wslpath", "-w", path], text=True, timeout=5).strip()
    if not re.match(r"^[A-Za-z]:\\", path):
        raise ValueError("Windows Vivado requires a drive-backed path, e.g. /mnt/f/... (not /tmp or UNC).")
    return path


def detect_root():
    configured = os.environ.get("VIVADO_WINDOWS_ROOT", "").strip()
    if configured:
        return Path(local_path(configured))
    candidates = []
    for var in ("XILINX_VIVADO", "VIVADO_HOME"):
        if os.environ.get(var):
            candidates.append(Path(local_path(os.environ[var])))
    for name in ("vivado", "vivado.bat", "xvlog", "xvlog.bat"):
        executable = shutil.which(name)
        if executable:
            candidates.append(Path(executable).parent.parent)
    for root in candidates:
        if (root / "bin" / "vivado.bat").is_file():
            # A real Linux install takes precedence in auto mode.
            if os.name != "nt" and (root / "bin" / "unwrapped" / "lnx64.o" / "vivado").is_file():
                continue
            return root
    return None


def cmd_arguments(tool, args):
    # cmd.exe interprets metacharacters even though Python uses shell=False.
    values = [tool, *args]
    if any(re.search(r'["%!?&|<>^\r\n\x00]', item) for item in values):
        raise ValueError("Unsupported cmd.exe metacharacter in tool path or arguments.")
    command = " ".join('"' + item + '"' for item in values)
    return '/d /s /c "' + command + '"'


_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
$result = @{ exit_code = -1; timed_out = $false; error = '' }
$proc = $null
try {
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $cfg.command -WorkingDirectory $cfg.cwd -WindowStyle Hidden -RedirectStandardOutput $cfg.stdout -RedirectStandardError $cfg.stderr -PassThru
    $null = $proc.Handle
    if (-not $proc.WaitForExit([int]$cfg.timeout_ms)) {
        $result.timed_out = $true
        & "$env:SystemRoot\System32\taskkill.exe" /PID $proc.Id /T /F | Out-Null
        if (-not $proc.WaitForExit(5000)) { throw 'Process tree did not exit after taskkill.' }
        $result.exit_code = 124
    } else {
        $proc.WaitForExit()
        $result.exit_code = $proc.ExitCode
    }
} catch {
    $result.error = $_.Exception.Message
} finally {
    if ($null -ne $proc -and -not $proc.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $proc.Id /T /F | Out-Null
    }
    $result | ConvertTo-Json -Compress | Set-Content -LiteralPath $cfg.result -Encoding UTF8
}
"""


def read_log(path):
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class WindowsVivado:
    def __init__(self, root):
        if os.name != "nt" and not is_wsl():
            raise ValueError("Windows Vivado is supported only from Windows or WSL.")
        self.root = Path(root).resolve()
        self.powershell = shutil.which("powershell.exe")
        if not self.powershell:
            raise ValueError("powershell.exe unavailable; enable WSL Windows interoperability.")
        for name in ("vivado", "xvlog", "xelab"):
            if not (self.root / "bin" / (name + ".bat")).is_file():
                raise ValueError(f"Windows Vivado is missing {name}.bat in {self.root / 'bin'}")
        windows_path(self.root)
        default = Path(__file__).resolve().parents[2] / ".vivado-work"
        self.scratch = Path(local_path(os.environ.get("VIVADO_WINDOWS_TMP", str(default))))
        windows_path(self.scratch)
        self.scratch.mkdir(parents=True, exist_ok=True)

    def workdir(self, prefix):
        return tempfile.mkdtemp(prefix=prefix, dir=self.scratch)

    def run(self, tool, args, work, timeout_s):
        if timeout_s <= 0:
            return 124, "[agent] Windows Vivado time budget exhausted"
        work = Path(work)
        # Unique files prevent a stale status from being mistaken for this invocation.
        token = uuid.uuid4().hex
        result_path = work / (token + ".result.json")
        stdout = work / (token + ".stdout.log")
        stderr = work / (token + ".stderr.log")
        config = {
            "command": cmd_arguments(windows_path(tool), args),
            "cwd": windows_path(work),
            "stdout": windows_path(stdout), "stderr": windows_path(stderr),
            "result": windows_path(result_path),
            "timeout_ms": min(max(1, int(timeout_s * 1000)), 2147483647),
        }
        payload = base64.b64encode(json.dumps(config).encode("utf-8")).decode("ascii")
        script = _POWERSHELL.replace("__CONFIG__", payload)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            proc = subprocess.run(
                [self.powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-EncodedCommand", encoded],
                cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout_s + 20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return -1, f"[agent] Windows Vivado bridge failed: {type(exc).__name__}"
        log = read_log(stdout) + "\n" + read_log(stderr)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return -1, "[agent] Windows runner did not produce a result: " + proc.stdout.decode("utf-8", errors="replace")[-2000:]
        if result["error"]:
            return -1, log + "\n[agent] Windows runner error: " + result["error"]
        if result["timed_out"]:
            return 124, log + f"\n[agent] Windows process tree terminated after {timeout_s:.1f}s"
        if type(result["exit_code"]) is not int:
            return -1, log + "\n[agent] Windows runner returned no exit code"
        return result["exit_code"], log
