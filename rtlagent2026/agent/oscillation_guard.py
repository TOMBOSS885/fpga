"""Prevents infinite repair oscillation and enforces solution quality monotonicity."""

from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple


class QualityRollbackGuard:
    def __init__(self):
        self.history_hashes: List[str] = []
        self._hash_levels: Dict[str, int] = {}
        self._last_hash: str = ""
        self.best_level: int = -1  # 0: L0, 1: L1, 2: L2, 3: L3
        self.best_code: str = ""
        self.best_round: int = 0

    @staticmethod
    def _hash(code: str) -> str:
        # Strip all whitespace to prevent trivial formatting oscillation
        normalized = "".join(code.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def record_attempt(self, code: str, current_level: int, round_num: int) -> Tuple[bool, str]:
        """Record a candidate and report only genuine repair cycles.

        The orchestrator records the same candidate at multiple milestones
        (for example after preflight and again before simulation).  Those
        duplicate observations are progress bookkeeping, not oscillation.  A
        cycle is reported only when a previously seen candidate reappears at
        the same or lower milestone after another candidate was tried.
        """
        h = self._hash(code)
        prior_level = self._hash_levels.get(h)
        repeated_after_other = bool(prior_level is not None and h != self._last_hash)

        if prior_level is None:
            self.history_hashes.append(h)
            self._hash_levels[h] = current_level
        elif current_level > prior_level:
            # The same source can legitimately pass a higher milestone later.
            self._hash_levels[h] = current_level
        self._last_hash = h

        if current_level >= self.best_level:
            self.best_level = current_level
            self.best_code = code
            self.best_round = round_num

        if repeated_after_other and current_level <= prior_level:
            return True, "OSCILLATION: candidate returned after another repair attempt."
        if prior_level is not None:
            if current_level > prior_level:
                return False, "DUPLICATE_SOURCE: higher milestone recorded."
            return False, "DUPLICATE_SOURCE: milestone already recorded."
        return False, "PROGRESS_NORMAL"

    def get_best_deliverable(self, final_code: str, final_level: int) -> Tuple[str, int]:
        """Ensure we never return a regressed version if a later repair failed."""
        if final_level < self.best_level and self.best_code:
            return self.best_code, self.best_level
        return final_code or self.best_code, max(final_level, self.best_level)
