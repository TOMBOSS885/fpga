"""Measure AMD VRAM via Linux amdgpu sysfs on the actual model host."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from agent.trace import atomic_write


def measure(root=Path("/sys/class/drm"), card: str = "") -> dict:
    candidates = [p for p in root.glob("card[0-9]*") if p.name[4:].isdigit()]
    amd = []
    for p in candidates:
        try:
            if (p / "device/vendor").read_text().strip() == "0x1002":
                amd.append(p)
        except OSError:
            continue
    if card:
        amd = [p for p in amd if p.name == card]
    if len(amd) != 1:
        raise ValueError("Select one AMD GPU using GPU_DRM_CARD (for example card0)")
    gpu = amd[0]
    used = int((gpu / "device/mem_info_vram_used").read_text())
    total = int((gpu / "device/mem_info_vram_total").read_text())
    return dict(timestamp=time.time(), vram_gb=round(used / 1024**3, 4),
                total_vram_gb=round(total / 1024**3, 4), gpu=gpu.name,
                source="amdgpu_sysfs_current_usage_not_peak")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    while True:
        atomic_write(output, json.dumps(measure(card=os.environ.get("GPU_DRM_CARD", ""))) + "\n")
        if not args.watch:
            break
        time.sleep(10)


if __name__ == "__main__":
    main()
