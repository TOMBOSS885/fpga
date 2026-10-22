"""Skill pack management and error/context-driven semantic selection."""

from __future__ import annotations

import os
import re
from typing import List


# These aliases keep skill selection useful when the task uses natural
# language rather than the exact log signature from a SKILL.md.  They encode
# the same trigger vocabulary used by the RTL skills, without hard-coding a
# solution for any particular benchmark item.
_DOMAIN_ALIASES = {
    "rtl-arithmetic-truncation": (
        "popcount", "population", "bit count", "overflow", "underflow",
        "truncat", "signed", "carry", "arithmetic", "count bits",
    ),
    "rtl-clock-reset-conventions": (
        "clock", "clk", "posedge", "negedge", "reset", "rst", "asynchronous",
        "synchronous", "clock domain",
    ),
    "rtl-control-priority-pattern": (
        "priority", "load", "enable", "clear", "seed", "simultaneous",
    ),
    "rtl-fsm-idioms": (
        "fsm", "state machine", "state transition", "finite state", "sequence",
    ),
    "rtl-golden-scoreboard-verifier": (
        "scoreboard", "golden", "reference", "assertion", "mismatch", "self-check",
    ),
    "rtl-interface-contract": (
        "interface", "module", "port", "topmodule", "xelab", "vrfc",
    ),
    "rtl-self-testbench-generator": (
        "testbench", "xsim", "simulate", "simulation", "test vector", "waveform",
    ),
    "rtl-shift-register-pattern": (
        "shift register", "lfsr", "linear feedback", "sequence detector", "overlap",
    ),
    "rtl-synthesis-latch-prevention": (
        "latch", "synthesis", "synth", "multi-driven", "always @", "combinational",
    ),
}


class Skill:
    def __init__(
        self,
        name: str,
        description: str,
        signatures: list[str],
        trigger: str,
        body: str,
        path: str,
    ):
        self.name = name
        self.description = description
        self.signatures = signatures
        self.trigger = trigger
        self.body = body
        self.path = path

    def relevance_score(self, *texts: str) -> int:
        """Calculate relevance score against given contexts (prompt, error log, etc.)."""
        haystack = " ".join(t.lower() for t in texts if t)
        if not haystack:
            return 0

        score = 0
        # 1. Exact signature hit (+10)
        for sig in self.signatures:
            sig = sig.strip().lower()
            if sig and sig in haystack:
                score += 10

        # 2. Trigger keywords (+5)
        if self.trigger:
            for word in re.split(r"[,、;；/\s]+", self.trigger.lower()):
                if len(word) >= 2 and word in haystack:
                    score += 5

        # 3. Name tokens (+4)
        for token in self.name.lower().replace("-", " ").split():
            if len(token) >= 3 and token in haystack:
                score += 4

        # 4. Description tokens (+1)
        for token in set(self.description.lower().split()):
            if len(token) >= 4 and token in haystack:
                score += 1

        # 5. Natural-language domain aliases (+3).  A single alias is enough
        # to activate a skill, but repeated words do not swamp exact errors.
        for alias in _DOMAIN_ALIASES.get(self.name, ()):
            if alias in haystack:
                score += 3
        return score

    def matches(self, *texts: str) -> bool:
        return self.relevance_score(*texts) > 0

    def render(self, budget_chars: int = 4000) -> str:
        trimmed = self.body.strip()
        if len(trimmed) > budget_chars:
            trimmed = trimmed[:budget_chars] + "\n...[truncated for token budget]"
        return f'<skill name="{self.name}">\n{trimmed}\n</skill>'


def _split_front_matter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
            meta = {}
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("'\"")
            return meta, body
    return {}, raw


def _extract_bullet_items(body: str, label: str) -> list[str]:
    m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.*)", body)
    if not m:
        return []
    line = m.group(1).strip()
    items = re.findall(r"`([^`]+)`", line)
    if not items:
        items = [x.strip() for x in line.split(",") if x.strip()]
    return items


def _extract_bullet_text(body: str, label: str) -> str:
    m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.*)", body)
    return m.group(1).strip() if m else ""


def load_skills(skill_dir: str) -> list[Skill]:
    skills: list[Skill] = []
    if not os.path.isdir(skill_dir):
        return skills

    for entry in sorted(os.listdir(skill_dir)):
        path = os.path.join(skill_dir, entry, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            continue

        meta, body = _split_front_matter(raw)
        skills.append(
            Skill(
                name=meta.get("name", entry),
                description=meta.get("description", ""),
                signatures=_extract_bullet_items(body, "Log signatures"),
                trigger=_extract_bullet_text(body, "Trigger"),
                body=body,
                path=path,
            )
        )
    return skills


def select_skills(skills: list[Skill], *context: str, limit: int = 2) -> list[Skill]:
    """Rank skills by relevance and return a diverse, stable shortlist.

    The first result is the strongest diagnosis match.  The second result is
    selected from a different skill family when possible, which mirrors the
    planner/tool separation used by modern coding agents: one skill explains
    the failure and one supplies the safe implementation pattern.
    """
    context_text = " ".join(x.lower() for x in context if x)
    scored = [(s, s.relevance_score(*context)) for s in skills]
    scored = [item for item in scored if item[1] > 0]
    # Explicit architecture intent outranks incidental words in prose.  For
    # example, a combinational popcount prompt often says "no clock"; that
    # must not make the clock/reset skill outrank bit-width guidance.
    mode_boost = {
        "combinational_tree": {"rtl-arithmetic-truncation": 30, "rtl-synthesis-latch-prevention": 8},
        "shift_register": {"rtl-shift-register-pattern": 30, "rtl-control-priority-pattern": 12},
        "three_process_fsm": {"rtl-fsm-idioms": 30, "rtl-clock-reset-conventions": 10},
        "counter": {"rtl-control-priority-pattern": 15, "rtl-arithmetic-truncation": 12},
    }
    for idx, (skill, score) in enumerate(scored):
        for mode, boosts in mode_boost.items():
            if mode in context_text:
                scored[idx] = (skill, score + boosts.get(skill.name, 0))
                break
    scored.sort(key=lambda x: (-x[1], x[0].name))
    selected: list[Skill] = []
    for skill, _score in scored:
        if len(selected) >= limit:
            break
        # Avoid returning two near-identical implementation skills when a
        # contract or verification skill also matched the same failure.
        family = skill.name.split("-", 2)[-1]
        if selected and family == selected[0].name.split("-", 2)[-1]:
            continue
        selected.append(skill)
    if len(selected) < limit:
        for skill, _score in scored:
            if skill not in selected:
                selected.append(skill)
                if len(selected) >= limit:
                    break
    return selected
