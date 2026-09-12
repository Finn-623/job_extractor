from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter


class DiscoveryBudgetExceeded(RuntimeError):
    """Raised when the shared discovery deadline has been exhausted."""


DEFAULT_PHASE_LIMITS = {
    "INITIAL_NAVIGATION": 20.0,
    "INITIAL_EVIDENCE": 8.0,
    "RECRUITMENT_ACTION_DISCOVERY": 5.0,
    "ACTION_EXECUTION": 8.0,
    "POST_ACTION_OBSERVATION": 8.0,
    "SOURCE_SELECTION": 8.0,
    "FINAL_CLASSIFICATION": 3.0,
}

UPSTREAM_DISCOVERY_SECONDS = 65.0
TERMINAL_ACTIVATION_SECONDS = 25.0
TERMINAL_PHASE_LIMITS = {
    "INITIAL_NAVIGATION": 8.0,
    "INITIAL_EVIDENCE": 5.0,
    "RECRUITMENT_ACTION_DISCOVERY": 3.0,
    "ACTION_EXECUTION": 4.0,
    "POST_ACTION_OBSERVATION": 4.0,
    "SOURCE_SELECTION": 4.0,
    "FINAL_CLASSIFICATION": 2.0,
}


@dataclass
class DiscoveryBudget:
    """One monotonic budget shared by redirects, actions, retries and hops."""

    total_seconds: float = 90.0
    phase_limits: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PHASE_LIMITS))
    started: float = field(default_factory=perf_counter)

    @property
    def elapsed(self) -> float:
        return max(0.0, perf_counter() - self.started)

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0.0

    def check(self) -> None:
        if self.expired:
            raise DiscoveryBudgetExceeded("GLOBAL_DISCOVERY_TIMEOUT")

    def seconds(self, phase: str, requested: float | None = None, minimum: float = 0.05) -> float:
        self.check()
        cap = self.phase_limits.get(phase, self.remaining)
        value = min(self.remaining, cap, requested if requested is not None else cap)
        if value < minimum:
            raise DiscoveryBudgetExceeded("GLOBAL_DISCOVERY_TIMEOUT")
        return value

    def milliseconds(self, phase: str, requested: int | None = None, minimum: int = 50) -> int:
        seconds = None if requested is None else requested / 1000
        return max(minimum, int(self.seconds(phase, seconds, minimum / 1000) * 1000))

def terminal_activation_budget(total_seconds:float=TERMINAL_ACTIVATION_SECONDS)->DiscoveryBudget:
    return DiscoveryBudget(total_seconds=total_seconds,phase_limits=dict(TERMINAL_PHASE_LIMITS))
