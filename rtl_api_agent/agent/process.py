"""Wall-clock bounds and process-tree cleanup, including interrupted requests."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


class Deadline:
    def __init__(self, seconds: float, reserve: float = 0):
        self.started = time.monotonic()
        self.end = self.started + max(0.0, seconds - reserve)

    @property
    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())

    def cap(self, seconds: float, reserve: float = 0.0) -> float:
        return max(0.0, min(seconds, self.remaining - reserve))


def stop_tree(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        # Descendants may still be alive after the immediate process exits.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=3)
    proc.wait()


def run(command: list[str], cwd: Path, timeout: float, log: Path,
        stdin: bytes | None = None, env: dict | None = None) -> tuple[int, str]:
    if timeout <= 0:
        return 124, "Wall-clock budget exhausted"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as output:
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env,
                                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=os.name == "posix")
        except OSError:
            return 127, "Executable unavailable"
        try:
            proc.communicate(input=stdin, timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            stop_tree(proc)
            rc = 124
        except BaseException:
            stop_tree(proc)
            raise
    with log.open("rb") as fh:
        # Keep memory bounded even if a faulty tool writes a huge log.
        size = fh.seek(0, 2)
        fh.seek(max(0, size - 512 * 1024))
        text = fh.read().decode("utf-8", errors="replace")
    if rc == 124:
        text += "\nWall-clock budget exhausted"
    return rc, text
