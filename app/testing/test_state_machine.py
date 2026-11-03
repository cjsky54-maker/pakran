"""Pure (Qt-free) state machine for the Standard Test workflow.

Phases:
IDLE -> WAITING_RISE -> MEASURING -> FINISHED
Waiting may end directly in FINISHED (timeout, camera lost, abort).
"""
from __future__ import annotations

from enum import Enum


class TestPhase(Enum):
    IDLE = "idle"
    WAITING_RISE = "waiting_rise"
    MEASURING = "measuring"
    FINISHED = "finished"


class InvalidTransition(Exception):
    pass


_VALID = {
    TestPhase.IDLE: {TestPhase.WAITING_RISE, TestPhase.FINISHED},
    TestPhase.WAITING_RISE: {TestPhase.MEASURING, TestPhase.FINISHED},
    TestPhase.MEASURING: {TestPhase.FINISHED},
    TestPhase.FINISHED: {TestPhase.IDLE},
}


class TestStateMachine:
    """Tracks the current phase and validates transitions."""

    def __init__(self) -> None:
        self._phase = TestPhase.IDLE
        self.history: list[tuple[TestPhase, TestPhase]] = []

    @property
    def phase(self) -> TestPhase:
        return self._phase

    def can(self, new_phase: TestPhase) -> bool:
        return new_phase in _VALID[self._phase]

    def transition(self, new_phase: TestPhase) -> None:
        if not self.can(new_phase):
            raise InvalidTransition(
                f"Illegal transition {self._phase.value} -> {new_phase.value}"
            )
        self.history.append((self._phase, new_phase))
        self._phase = new_phase

    def reset(self) -> None:
        self._phase = TestPhase.IDLE
        self.history.clear()
