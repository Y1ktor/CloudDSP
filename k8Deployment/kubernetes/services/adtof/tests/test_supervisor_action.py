"""Unit tests for ADTOF's injected supervisor wait/action boundary.

The waiter is a deterministic recording fake. These tests never sleep, install
signals, connect to a service, run ADTOF, create a loop, build an image, or
change Kubernetes state.
"""

from __future__ import annotations

import unittest

from app.supervisor_action import ADTOFSupervisorActionOutcome, apply_adtof_supervisor_decision
from app.supervisor_backoff import (
    DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
    ADTOFSupervisorAction,
    ADTOFSupervisorBackoffState,
    ADTOFSupervisorDecision,
)


class RecordingShutdownWaiter:
    """Return a controlled status while retaining each requested policy delay."""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.delays: list[float] = []

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Record the requested duration without actually blocking a test."""

        self.delays.append(timeout_seconds)
        return self.result


class ADTOFSupervisorActionTests(unittest.TestCase):
    """Prove each decision causes zero or one interruptible wait."""

    def test_immediate_progress_continues_without_waiting(self) -> None:
        """Normal queue progress must not add a latency gap before another poll."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_adtof_supervisor_decision(
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=0.0,
                next_state=ADTOFSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorActionOutcome.CONTINUE)
        self.assertEqual(waiter.delays, [])

    def test_idle_and_retry_wait_once_then_continue_after_timeout(self) -> None:
        """The waiter receives the exact finite delay selected by policy."""

        decisions = (
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
                next_state=ADTOFSupervisorBackoffState(),
            ),
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
                delay_seconds=4.0,
                next_state=ADTOFSupervisorBackoffState(retryable_failure_streak=3),
            ),
        )
        for decision in decisions:
            with self.subTest(action=decision.action):
                waiter = RecordingShutdownWaiter(result=False)
                result = apply_adtof_supervisor_decision(decision, shutdown_waiter=waiter)
                self.assertEqual(result.outcome, ADTOFSupervisorActionOutcome.CONTINUE)
                self.assertEqual(waiter.delays, [decision.delay_seconds])

    def test_shutdown_interrupts_idle_or_backoff_before_another_step(self) -> None:
        """A future SIGTERM event can stop safely before another broker receive."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_adtof_supervisor_decision(
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
                next_state=ADTOFSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.action, ADTOFSupervisorAction.WAIT_IDLE)
        self.assertEqual(waiter.delays, [DEFAULT_ADTOF_IDLE_DELAY_SECONDS])

    def test_fatal_configuration_exits_without_waiting(self) -> None:
        """A bad mounted Secret or image must not hide behind a timeout."""

        waiter = RecordingShutdownWaiter(result=False)
        result = apply_adtof_supervisor_decision(
            ADTOFSupervisorDecision(
                action=ADTOFSupervisorAction.EXIT_FATAL,
                delay_seconds=0.0,
                next_state=ADTOFSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorActionOutcome.EXIT_FATAL)
        self.assertEqual(waiter.delays, [])

    def test_invalid_waiter_or_output_is_rejected_without_truthiness_coercion(self) -> None:
        """A malformed waiter cannot silently stop or resume a future process."""

        decision = ADTOFSupervisorDecision(
            action=ADTOFSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_ADTOF_IDLE_DELAY_SECONDS,
            next_state=ADTOFSupervisorBackoffState(),
        )
        with self.assertRaises(TypeError):
            apply_adtof_supervisor_decision(decision, shutdown_waiter=object())  # type: ignore[arg-type]

        waiter = RecordingShutdownWaiter(result=False)
        waiter.result = 1  # type: ignore[assignment]
        with self.assertRaises(TypeError):
            apply_adtof_supervisor_decision(decision, shutdown_waiter=waiter)


if __name__ == "__main__":
    unittest.main()
