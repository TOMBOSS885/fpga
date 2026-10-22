"""Shared Vivado discovery and launch environment for the runner and terminal."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def discover_vivado_bin(env: dict[str, str]) -> str | None:
    if env.get("VIVADO_BIN", "").strip():
        return env["VIVADO_BIN"].strip()
    if env.get("XILINX_VIVADO", "").strip():
        return str(Path(env["XILINX_VIVADO"].strip()) / "bin")
    inherited = shutil.which("vivado", path=env.get("PATH", ""))
    if inherited:
        return str(Path(inherited).absolute().parent)
    candidates = [
        Path.home() / "vivado26.1" / "2026.1" / "Vivado" / "bin",
        Path("/opt/Xilinx/2026.1/Vivado/bin"),
        Path("/tools/Xilinx/2026.1/Vivado/bin"),
    ]
    return next((str(path) for path in candidates if (path / "vivado").is_file()), None)


def runtime_env(env: dict[str, str], vivado_bin: Path | None) -> dict[str, str]:
    current = dict(env, XILINX_LOCAL_USER_DATA="NO")
    if vivado_bin:
        # Ubuntu 26 is not recognized by the 2026.1 distro probe. Use shipped
        # compatibility libraries when present; this does not imply OS support.
        lib_root = vivado_bin.resolve().parent / "lib" / "lnx64.o"
        compatibility = next((lib_root / name for name in ("Ubuntu/24", "Ubuntu/22", "Rhel/10", "Rhel/9", "SuSE")
                              if (lib_root / name / "libncurses.so.5").exists()), None)
        if compatibility:
            paths = [current.get("LD_LIBRARY_PATH", ""), str(compatibility), str(lib_root)]
            current["LD_LIBRARY_PATH"] = os.pathsep.join(path for path in paths if path)
    return current
