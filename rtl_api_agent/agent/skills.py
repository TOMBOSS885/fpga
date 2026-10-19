"""Small on-demand skill context following the official Agent Skills format."""
from __future__ import annotations

import re
from pathlib import Path


def select(root: Path, query: str, limit: int = 3, max_chars: int = 6000) -> tuple[str, list[str]]:
    words = set(re.findall(r"[a-z0-9_]+", query.lower()))
    ranked = []
    for path in sorted(root.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        terms = set(re.findall(r"[a-z0-9_]+", (path.parent.name + " " + text[:1400]).lower()))
        score = len(words & terms)
        if path.parent.name == "rtl-module-interface-contract":
            score += 10
        ranked.append((score, path.parent.name, text))
    ranked.sort(key=lambda v: (-v[0], v[1]))
    selected, blocks, used = [], [], 0
    picks = [item for item in ranked if item[0] > 0][:limit]
    for index, (score, name, text) in enumerate(picks):
        header = f"### {name}\n"
        # A long official contract must not starve arithmetic/reset skills.
        allowance = (max_chars - used) // (len(picks) - index)
        excerpt = text[:max(0, allowance - len(header) - 2)]
        if not excerpt:
            break
        blocks.append(header + excerpt)
        selected.append(name)
        used += len(header) + len(excerpt) + 2
    return "\n\n".join(blocks), selected
