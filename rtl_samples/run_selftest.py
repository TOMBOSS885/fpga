#!/usr/bin/env python3
"""Launch the existing official selftest driver, accepting its CRLF checkout."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    driver = Path(__file__).resolve().parents[1] / "rtlagent2026/selftest/run_selftest.sh"
    # read_text translates CRLF in memory. With bash -c, $0 names the original
    # driver so its HERE/ROOT, env.sh, judge and agent resolution stay intact.
    script = driver.read_text(encoding="utf-8")
    marker = "${BASH_SOURCE[0]}"
    if script.count(marker) != 1:
        raise ValueError("Official selftest driver changed; review the launcher")
    script = script.replace(marker, "$0", 1)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    os.execvpe("bash", ["bash", "-c", script, str(driver), *sys.argv[1:]], env)


if __name__ == "__main__":
    main()
