"""Dynamic wall-clock time and round budget controller for competition optimization."""

from __future__ import annotations

import time


class DynamicBudgetController:
    def __init__(self, deadline_s: float = 360.0, reserve_s: float = 20.0, max_rounds: int = 4):
        self.deadline_s = deadline_s
        self.reserve_s = reserve_s
        self.max_rounds = max_rounds
        self.start_time = time.time()
        self.round = 0

    @property
    def elapsed_s(self) -> float:
        return time.time() - self.start_time

    @property
    def deadline_at(self) -> float:
        return self.start_time + self.deadline_s - self.reserve_s

    @property
    def remaining_pool_s(self) -> float:
        return max(0.0, self.deadline_s - self.elapsed_s - self.reserve_s)

    def should_continue(self) -> bool:
        if self.round >= self.max_rounds:
            return False
        return self.remaining_pool_s > 8.0  # At least 8s for emergency wrap-up

    def next_round(self) -> int:
        self.round += 1
        return self.round

    def allocate_timeout(self, step_type: str) -> float:
        """Dynamic slice allocation based on remaining time and operation weight."""
        rem = self.remaining_pool_s
        rounds_left = max(1, self.max_rounds - self.round + 1)
        round_budget = rem / rounds_left

        if step_type == "lint":
            return min(rem, max(8.0, min(round_budget * 0.25, 45.0)))
        elif step_type == "sim":
            return min(rem, max(10.0, min(round_budget * 0.40, 75.0)))
        elif step_type == "synth":
            # Synthesis is computationally expensive, allocate conservatively
            return min(rem, max(15.0, min(round_budget * 0.65, 120.0)))
        elif step_type == "llm":
            return min(rem, max(8.0, min(round_budget * 0.25, 30.0)))
        return min(rem, 30.0)
