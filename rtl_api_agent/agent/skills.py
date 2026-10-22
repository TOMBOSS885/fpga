"""Small on-demand skill context following the official Agent Skills format."""
from __future__ import annotations

import re
from pathlib import Path

from .artifacts import TaskProfile


STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "only", "must",
    "should", "using", "use", "module", "named", "output", "input", "implement",
    "logic", "signal", "value", "values", "when", "then", "than", "your", "you",
}

# Score one semantic risk once.  Expanding signed/width into many synonyms
# otherwise lets a broad skill crowd out a task's FSM or asynchronous reset.
RISK_WEIGHTS = {
    "rtl-arithmetic-truncation": {"arithmetic": 4, "width_signedness": 6},
    "rtl-clock-reset-conventions": {"clocked": 3, "reset": 4, "async_reset": 7, "active_low": 4},
    "rtl-control-priority-pattern": {"control_priority": 3},
    "rtl-fsm-idioms": {"fsm": 16},
    "rtl-shift-register-pattern": {"sequence": 12, "lfsr": 16},
}


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9_]+", text.lower()) if len(word) > 2 and word not in STOPWORDS}


def _front_matter(text: str) -> str:
    """Return trigger metadata without requiring a YAML dependency."""
    head = text[:2200]
    match = re.search(r"^---\s*\n(.*?)\n---", head, re.S | re.M)
    triggers = re.findall(r"(?im)^- \*\*(?:Trigger|Log signatures):\*\*[^\n]*", head)
    return "\n".join([match.group(1) if match else head.split("```", 1)[0], *triggers])


def select(root: Path, query: str, limit: int = 3, max_chars: int = 6000,
           profile: TaskProfile | None = None, phase: str = "",
           failure: str = "", failure_category: str = "") -> tuple[str, list[str]]:
    """Select bounded skill context using task risks and current phase.

    Keyword overlap remains the fallback so old callers and third-party skills
    continue to work.  Profile tags and compiler log signatures add signal
    when the task wording is terse (a common pattern in benchmark prompts).
    """
    words = _tokens(query)
    profile_tags = set(profile.tags) if profile else set()
    failure_words = _tokens(failure)
    ranked = []
    for path in sorted(root.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        metadata = _front_matter(text)
        # Front matter is the skill's routing contract. Avoid scoring on a
        # generic example body (e.g. a shift-register skill mentioning FSM).
        terms = _tokens(path.parent.name + " " + metadata)
        score = min(4, len(words & terms)) + 2 * min(4, len(failure_words & terms))
        score += sum(weight for tag, weight in RISK_WEIGHTS.get(path.parent.name, {}).items()
                     if tag in profile_tags)
        if path.parent.name == "rtl-synthesis-latch-prevention" and (phase == "synth_repair" or failure_category == "synthesis"):
            score += 24
        if path.parent.name == "rtl-control-priority-pattern" and words & {"priority", "load", "clear"}:
            score += 10
        ranked.append((score, path.parent.name, text))
    # Reserve a slot for the public interface contract independently of scores.
    ranked.sort(key=lambda v: (v[1] != "rtl-module-interface-contract", -v[0], v[1]))
    selected, blocks, used = [], [], 0
    picks = [item for item in ranked if item[0] > 0 or item[1] == "rtl-module-interface-contract"][:max(0, limit)]
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
