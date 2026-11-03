"""TestStateMachine transitions."""
import pytest

from app.testing.test_state_machine import (
    InvalidTransition,
    TestPhase,
    TestStateMachine,
)


def test_normal_flow():
    sm = TestStateMachine()
    sm.transition(TestPhase.WAITING_RISE)
    sm.transition(TestPhase.MEASURING)
    sm.transition(TestPhase.FINISHED)
    assert sm.phase == TestPhase.FINISHED
    assert len(sm.history) == 3


def test_waiting_may_finish_directly():
    sm = TestStateMachine()
    sm.transition(TestPhase.WAITING_RISE)
    sm.transition(TestPhase.FINISHED)
    assert sm.phase == TestPhase.FINISHED


def test_illegal_transitions_raise():
    sm = TestStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition(TestPhase.MEASURING)  # cannot skip waiting
    sm.transition(TestPhase.WAITING_RISE)
    with pytest.raises(InvalidTransition):
        sm.transition(TestPhase.WAITING_RISE)  # no self-loop
    with pytest.raises(InvalidTransition):
        sm.transition(TestPhase.IDLE)


def test_can_and_reset():
    sm = TestStateMachine()
    assert sm.can(TestPhase.WAITING_RISE)
    assert not sm.can(TestPhase.MEASURING)
    sm.transition(TestPhase.WAITING_RISE)
    sm.transition(TestPhase.FINISHED)
    assert sm.can(TestPhase.IDLE)
    sm.reset()
    assert sm.phase == TestPhase.IDLE
    assert sm.history == []
