"""Unit tests for the injected Basic Pitch supervisor wait/action boundary.

The waiter is a local recording fake. The tests never sleep, register signals,
connect to a service, run a model, create a worker loop, or change Kubernetes.
"""

from __future__ import annotations

import unittest

from app.supervisor_action import (
    BasicPitchSupervisorActionOutcome,
    apply_basic_pitch_supervisor_decision,
)
from app.supervisor_backoff import (
    DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
    BasicPitchSupervisorAction,
    BasicPitchSupervisorBackoffState,
    BasicPitchSupervisorDecision,
)


class RecordingShutdownWaiter:
    """Return controlled shutdown status and retain every requested delay."""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.delays: list[float] = []

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        self.delays.append(timeout_seconds)
        return self.result


class BasicPitchSupervisorActionTests(unittest.TestCase):
    """Prove decisions cause zero or one injected interruptible wait only."""

    def test_immediate_progress_continues_without_waiting(self) -> None:
        """Normal progress must not add latency before the next fair check."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_basic_pitch_supervisor_decision(
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=0.0,
                next_state=BasicPitchSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, BasicPitchSupervisorActionOutcome.CONTINUE)
        self.assertEqual(waiter.delays, [])

    def test_idle_and_retry_wait_once_then_continue_when_timeout_elapses(self) -> None:
        """The caller-owned waiter receives the exact bounded delay from policy."""

        decisions = (
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
                next_state=BasicPitchSupervisorBackoffState(),
            ),
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.RETRY_AFTER_BACKOFF,
                delay_seconds=4.0,
                next_state=BasicPitchSupervisorBackoffState(retryable_failure_streak=3),
            ),
        )
        for decision in decisions:
            with self.subTest(action=decision.action):
                waiter = RecordingShutdownWaiter(result=False)
                result = apply_basic_pitch_supervisor_decision(decision, shutdown_waiter=waiter)
                self.assertEqual(result.outcome, BasicPitchSupervisorActionOutcome.CONTINUE)
                self.assertEqual(waiter.delays, [decision.delay_seconds])

    def test_shutdown_interrupts_idle_or_backoff_before_another_worker_step(self) -> None:
        """A future SIGTERM event can stop the loop without waiting the full delay."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_basic_pitch_supervisor_decision(
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
                next_state=BasicPitchSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, BasicPitchSupervisorActionOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.action, BasicPitchSupervisorAction.WAIT_IDLE)
        self.assertEqual(waiter.delays, [DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS])

    def test_fatal_configuration_returns_exit_without_waiting(self) -> None:
        """A bad mounted Secret or image must not hide behind a timeout."""

        waiter = RecordingShutdownWaiter(result=False)
        result = apply_basic_pitch_supervisor_decision(
            BasicPitchSupervisorDecision(
                action=BasicPitchSupervisorAction.EXIT_FATAL,
                delay_seconds=0.0,
                next_state=BasicPitchSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, BasicPitchSupervisorActionOutcome.EXIT_FATAL)
        self.assertEqual(waiter.delays, [])

    def test_invalid_waiter_or_output_is_rejected_without_treating_it_as_shutdown(self) -> None:
        """Truthiness cannot accidentally stop or continue a worker process."""

        decision = BasicPitchSupervisorDecision(
            action=BasicPitchSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_BASIC_PITCH_IDLE_DELAY_SECONDS,
            next_state=BasicPitchSupervisorBackoffState(),
        )
        with self.assertRaises(TypeError):
            apply_basic_pitch_supervisor_decision(decision, shutdown_waiter=object())  # type: ignore[arg-type]

        waiter = RecordingShutdownWaiter(result=False)
        waiter.result = 1  # type: ignore[assignment]
        with self.assertRaises(TypeError):
            apply_basic_pitch_supervisor_decision(decision, shutdown_waiter=waiter)


if __name__ == "__main__":
    unittest.main()
