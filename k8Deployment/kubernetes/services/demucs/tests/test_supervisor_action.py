"""Unit tests for Demucs's injected supervisor wait/action boundary.

The waiter is a deterministic recording fake. Tests never sleep, install
signals, connect to a service, run FFprobe/Demucs, create a loop, build an
image, or change Kubernetes state.
"""

from __future__ import annotations

import unittest

from app.runtime.supervisor_action import DemucsSupervisorActionOutcome, apply_demucs_supervisor_decision
from app.runtime.supervisor_backoff import (
    DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
)


class RecordingShutdownWaiter:
    """Return a controlled status and retain each requested policy delay."""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.delays: list[float] = []

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Record a request without actually blocking the unit test."""

        self.delays.append(timeout_seconds)
        return self.result


class DemucsSupervisorActionTests(unittest.TestCase):
    """Prove each decision causes zero or one injected interruptible wait."""

    def test_immediate_progress_continues_without_waiting(self) -> None:
        """Progress must not add a latency gap before the next cycle."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_demucs_supervisor_decision(
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.CHECK_IMMEDIATELY,
                delay_seconds=0.0,
                next_state=DemucsSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, DemucsSupervisorActionOutcome.CONTINUE)
        self.assertEqual(waiter.delays, [])

    def test_idle_and_retry_wait_once_then_continue_after_timeout(self) -> None:
        """The waiter receives the exact finite delay selected by policy."""

        decisions = (
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
                next_state=DemucsSupervisorBackoffState(),
            ),
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
                delay_seconds=4.0,
                next_state=DemucsSupervisorBackoffState(retryable_failure_streak=3),
            ),
        )
        for decision in decisions:
            with self.subTest(action=decision.action):
                waiter = RecordingShutdownWaiter(result=False)
                result = apply_demucs_supervisor_decision(decision, shutdown_waiter=waiter)
                self.assertEqual(result.outcome, DemucsSupervisorActionOutcome.CONTINUE)
                self.assertEqual(waiter.delays, [decision.delay_seconds])

    def test_shutdown_interrupts_idle_or_backoff_before_another_cycle(self) -> None:
        """A future SIGTERM event can stop before another broker receive."""

        waiter = RecordingShutdownWaiter(result=True)
        result = apply_demucs_supervisor_decision(
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.WAIT_IDLE,
                delay_seconds=DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
                next_state=DemucsSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.action, DemucsSupervisorAction.WAIT_IDLE)
        self.assertEqual(waiter.delays, [DEFAULT_DEMUCS_IDLE_DELAY_SECONDS])

    def test_fatal_configuration_exits_without_waiting(self) -> None:
        """Invalid image/Secret configuration must not hide behind a timeout."""

        waiter = RecordingShutdownWaiter(result=False)
        result = apply_demucs_supervisor_decision(
            DemucsSupervisorDecision(
                action=DemucsSupervisorAction.EXIT_FATAL,
                delay_seconds=0.0,
                next_state=DemucsSupervisorBackoffState(),
            ),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, DemucsSupervisorActionOutcome.EXIT_FATAL)
        self.assertEqual(waiter.delays, [])

    def test_invalid_waiter_or_output_is_rejected_without_truthiness_coercion(self) -> None:
        """A malformed waiter cannot silently stop or resume a process."""

        decision = DemucsSupervisorDecision(
            action=DemucsSupervisorAction.WAIT_IDLE,
            delay_seconds=DEFAULT_DEMUCS_IDLE_DELAY_SECONDS,
            next_state=DemucsSupervisorBackoffState(),
        )
        with self.assertRaises(TypeError):
            apply_demucs_supervisor_decision(decision, shutdown_waiter=object())  # type: ignore[arg-type]

        waiter = RecordingShutdownWaiter(result=False)
        waiter.result = 1  # type: ignore[assignment]
        with self.assertRaises(TypeError):
            apply_demucs_supervisor_decision(decision, shutdown_waiter=waiter)


if __name__ == "__main__":
    unittest.main()
