#!/usr/bin/env python3
"""Materialize the pinned public dataset using the competition's importer."""
from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
UPSTREAM = ROOT / "official/verilog-eval-v2"
SOURCE = UPSTREAM / "dataset_spec-to-rtl"
IMPORTER = REPO / "rtlagent2026/selftest/veval_import.py"
REVISION = "c498220d0a52248f8e3fdffe279075215bde2da6"
KNOWN_ISSUES = {
    "Prob099_m2014_q6c": (
        "The prompt port list and RefModule declare Y1/Y3, but the testbench "
        "connects Y2/Y4. The prompt body also contradicts its own port list. "
        "Preserved without semantic edits and excluded from tasks/."
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source() -> list[str]:
    rows = (UPSTREAM / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    checked = set()
    for row in rows:
        expected, relative = row.split(maxsplit=1)
        path = (REPO / relative.strip()).resolve()
        if not path.is_relative_to(UPSTREAM.resolve()) or sha256(path) != expected:
            raise ValueError(f"Upstream checksum mismatch: {relative}")
        checked.add(path)
    names = sorted(path.name.removesuffix("_prompt.txt") for path in SOURCE.glob("*_prompt.txt"))
    if len(names) != 156 or not set(KNOWN_ISSUES).issubset(names):
        raise ValueError("Expected the pinned set of 156 source tasks")
    listed = (SOURCE / "problems.txt").read_text(encoding="utf-8").split()
    if sorted(listed) != names:
        raise ValueError("Source prompts differ from problems.txt")
    for name in names:
        for suffix in ("_prompt.txt", "_ref.sv", "_test.sv"):
            if (SOURCE / (name + suffix)).resolve() not in checked:
                raise ValueError(f"Source file missing from SHA256SUMS: {name + suffix}")
    return names


def expected_tree(staging: Path, names: list[str]) -> dict[str, bytes]:
    convert = runpy.run_path(str(IMPORTER))["convert_one"]
    entries = []
    for name in names:
        group = "known_issues" if name in KNOWN_ISSUES else "tasks"
        result = convert(str(SOURCE), name, str(staging / group))
        if not result["ok"] or not result.get("patched"):
            raise ValueError(f"Unexpected conversion result: {result}")
        entry = {"task_id": name, "path": f"{group}/{name}",
                 "status": "quarantined" if name in KNOWN_ISSUES else "included"}
        if name in KNOWN_ISSUES:
            entry["reason"] = KNOWN_ISSUES[name]
        entries.append(entry)
    manifest = {
        "schema_version": 1,
        "track": "rtl",
        "source": {"repository": "https://github.com/NVlabs/verilog-eval",
                   "revision": REVISION, "dataset": "dataset_spec-to-rtl",
                   "license": "LICENSE", "checksums": "official/verilog-eval-v2/SHA256SUMS"},
        "converter": {"path": "../rtlagent2026/selftest/veval_import.py",
                      "sha256": sha256(IMPORTER)},
        "part": "xczu3eg-sbva484-1-e",
        "period_ns": 5,
        "counts": {"source": len(names), "included": len(names) - len(KNOWN_ISSUES),
                   "quarantined": len(KNOWN_ISSUES)},
        "transformations": [
            "Normalize prompt outer whitespace using the official importer; do not add interface.txt.",
            "Remove the waveform-only $dumpfile/$dumpvars initial block for xvlog compatibility.",
            "Rename RefModule to TopModule only in reference/solution.sv for judge-chain checks.",
        ],
        "validation_scope": "Public development samples; inclusion does not certify functional or synthesis correctness.",
        "tasks": entries,
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (staging / "LICENSE").write_bytes((UPSTREAM / "LICENSE").read_bytes())
    return {str(path.relative_to(staging)): path.read_bytes()
            for path in staging.rglob("*") if path.is_file()}


def check_tree(expected: dict[str, bytes]) -> None:
    for relative, data in expected.items():
        path = ROOT / relative
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError(f"Missing or modified generated sample: {relative}")
    for group in ("tasks", "known_issues"):
        actual = {str(path.relative_to(ROOT)) for path in (ROOT / group).rglob("*") if path.is_file()}
        wanted = {path for path in expected if path.startswith(group + "/")}
        if actual != wanted:
            raise ValueError(f"Unexpected files in {group}/: {sorted(actual - wanted)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify source and generated files without updating them")
    args = parser.parse_args()
    names = verify_source()
    with tempfile.TemporaryDirectory(prefix="rtl_samples_") as directory:
        expected = expected_tree(Path(directory), names)
        if not args.check:
            # Preserve any local edits; regeneration must never silently erase them.
            for relative, data in expected.items():
                path = ROOT / relative
                if path.exists() and (not path.is_file() or path.read_bytes() != data):
                    raise ValueError(f"Refusing to overwrite a modified file: {relative}")
            for relative, data in expected.items():
                path = ROOT / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        check_tree(expected)
    print("OK: 156 source tasks verified; 155 tasks ready; 1 inconsistent fixture quarantined.")
    print("Official importer output, metadata, license and absence of interface.txt verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
