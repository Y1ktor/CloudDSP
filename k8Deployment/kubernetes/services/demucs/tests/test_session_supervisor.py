"""Unit tests for per-normal-turn Demucs AMQP session lifecycle.

All broker/session and worker-cycle seams are mocked. These tests do not open a
socket, sleep, consume, acknowledge, publish, run a model, or touch Kubernetes.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.amqp_connection import DemucsAMQPConnectionUnavailable
from app.recovery_cadence import DemucsWorkerCadenceAction, DemucsWorkerCadenceState
from app.session_supervisor import run_demucs_session_supervisor_until_stop
from app.shutdown_event import DemucsShutdownWaiter
from app.supervisor_action import DemucsSupervisorActionOutcome, DemucsSupervisorActionResult
from app.supervisor_backoff import (
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
)
from app.supervisor_once import DemucsSupervisorOnceResult
from app.supervisor_step import DemucsSupervisorStepResult, DemucsSupervisorStepState


def state(action: DemucsWorkerCadenceAction) -> DemucsSupervisorStepState:
    """Create deterministic supervisor state with one explicit next action."""

    return DemucsSupervisorStepState(
        backoff_state=DemucsSupervisorBackoffState(),
        cadence_state=DemucsWorkerCadenceState(action),
    )


def stopped_cycle(next_state: DemucsSupervisorStepState) -> DemucsSupervisorOnceResult:
    """Return one structurally valid shutdown-aware cycle result."""

    # A retry/backoff decision must carry its first nonzero local failure
    # streak. Keep the caller's cadence so each test controls which AMQP path
    # the session-owning loop takes before shutdown is observed.
    terminal_state = DemucsSupervisorStepState(
        backoff_state=DemucsSupervisorBackoffState(1),
        cadence_state=next_state.cadence_state,
    )
    decision = DemucsSupervisorDecision(
        action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
        delay_seconds=1.0,
        next_state=terminal_state.backoff_state,
    )
    step = DemucsSupervisorStepResult(
        event=DemucsSupervisorEvent.RETRYABLE_FAILURE,
        decision=decision,
        next_state=terminal_state,
    )
    return DemucsSupervisorOnceResult(
        step=step,
        action_result=DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED,
            action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
        ),
        next_state=terminal_state,
    )


class ShutdownOnPositiveWait(DemucsShutdownWaiter):
    """Avoid a real backoff while preserving the waiter's reviewed interface."""

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Continue zero-time prechecks but stop on the first backoff wait."""

        return timeout_seconds > 0


class DemucsSessionSupervisorTests(unittest.TestCase):
    """Prove recovery is socket-free and every normal poll owns one session."""

    @patch("app.session_supervisor.run_one_demucs_supervisor_cycle")
    def test_normal_turn_opens_and_closes_one_session_around_one_cycle(self, run_cycle) -> None:
        """A normal basic_get cannot inherit a channel from an earlier poll."""

        events: list[str] = []
        channel = MagicMock()

        @contextmanager
        def session():
            events.append("session-enter")
            yield channel
            events.append("session-exit")

        initial = state(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
        run_cycle.return_value = stopped_cycle(initial)

        result = run_demucs_session_supervisor_until_stop(
            initial_state=initial,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=DemucsShutdownWaiter(),
            session_factory=session,
        )

        self.assertEqual(result.completed_cycles, 1)
        self.assertEqual(events, ["session-enter", "session-exit"])
        self.assertIs(run_cycle.call_args.args[0], channel)

    @patch("app.session_supervisor.run_one_demucs_supervisor_cycle")
    def test_recovery_turn_uses_no_amqp_session(self, run_cycle) -> None:
        """Recovery must read durable PostgreSQL state without broker access."""

        initial = state(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)
        session_factory = MagicMock()
        run_cycle.return_value = stopped_cycle(initial)

        result = run_demucs_session_supervisor_until_stop(
            initial_state=initial,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=DemucsShutdownWaiter(),
            session_factory=session_factory,
        )

        self.assertEqual(result.completed_cycles, 1)
        session_factory.assert_not_called()

    @patch("app.session_supervisor.run_one_demucs_supervisor_cycle")
    def test_session_connection_failure_becomes_interruptible_reconnect_backoff(self, run_cycle) -> None:
        """A failed session neither changes cadence nor crashes silently."""

        initial = state(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)
        session_factory = MagicMock(
            side_effect=DemucsAMQPConnectionUnavailable("synthetic AMQP outage")
        )

        result = run_demucs_session_supervisor_until_stop(
            initial_state=initial,
            database=MagicMock(),
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=ShutdownOnPositiveWait(),
            session_factory=session_factory,
        )

        self.assertEqual(result.completed_cycles, 1)
        self.assertEqual(result.final_state.cadence_state, initial.cadence_state)
        self.assertEqual(
            result.final_cycle.action_result.outcome,
            DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED,
        )
        session_factory.assert_called_once_with()
        run_cycle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
