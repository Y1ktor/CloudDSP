"""Unit tests for one complete Demucs supervisor step/action composition.

The step and shutdown-aware action are patched at public seams. Tests do not
open services, wait, run FFprobe/Demucs, loop, build an image, or change
Kubernetes state.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import ANY, MagicMock, patch

from app.runtime.recovery_cadence import DemucsWorkerCadenceAction, DemucsWorkerCadenceState
from app.runtime.supervisor_action import DemucsSupervisorActionOutcome, DemucsSupervisorActionResult
from app.runtime.supervisor_backoff import (
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
)
from app.runtime.supervisor_once import DemucsSupervisorOnceResult, run_one_demucs_supervisor_cycle
from app.runtime.supervisor_step import DemucsSupervisorStepResult, DemucsSupervisorStepState


def retryable_step() -> DemucsSupervisorStepResult:
    """Return one complete classified step with no normal cycle evidence."""

    next_state = DemucsSupervisorStepState(
        backoff_state=DemucsSupervisorBackoffState(retryable_failure_streak=1),
        cadence_state=DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN),
    )
    return DemucsSupervisorStepResult(
        event=DemucsSupervisorEvent.RETRYABLE_FAILURE,
        decision=DemucsSupervisorDecision(
            action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
            delay_seconds=1.0,
            next_state=next_state.backoff_state,
        ),
        next_state=next_state,
    )


class DemucsSupervisorOnceTests(unittest.TestCase):
    """Prove every complete step gets exactly one matching control action."""

    @patch("app.runtime.supervisor_once.apply_demucs_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_demucs_supervisor_step")
    def test_forwards_step_decision_and_returns_its_next_state(self, run_step, apply_action) -> None:
        """The runner cannot substitute action or state after a completed step."""

        step = retryable_step()
        run_step.return_value = step
        action = DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.CONTINUE,
            action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
        )
        apply_action.return_value = action
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()
        waiter = MagicMock()
        demucs_runner = MagicMock()
        initial_state = DemucsSupervisorStepState()

        result = run_one_demucs_supervisor_cycle(
            channel,
            state=initial_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
            demucs_runner=demucs_runner,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
            jitter_fraction=0.25,
        )

        self.assertIs(result.step, step)
        self.assertIs(result.action_result, action)
        self.assertEqual(result.next_state, step.next_state)
        run_step.assert_called_once_with(
            channel,
            state=initial_state,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=None,
            demucs_runner=demucs_runner,
            uploader=None,
            event_id_factory=ANY,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
            jitter_fraction=0.25,
        )
        apply_action.assert_called_once_with(step.decision, shutdown_waiter=waiter)

    @patch("app.runtime.supervisor_once.apply_demucs_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_demucs_supervisor_step")
    def test_shutdown_or_continue_outcome_returns_without_another_step(self, run_step, apply_action) -> None:
        """A later entrypoint can stop/continue after this one observed action."""

        step = retryable_step()
        run_step.return_value = step
        for outcome in (
            DemucsSupervisorActionOutcome.SHUTDOWN_REQUESTED,
            DemucsSupervisorActionOutcome.CONTINUE,
        ):
            with self.subTest(outcome=outcome):
                apply_action.reset_mock(return_value=True, side_effect=True)
                apply_action.return_value = DemucsSupervisorActionResult(
                    outcome=outcome,
                    action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
                )
                result = run_one_demucs_supervisor_cycle(
                    MagicMock(),
                    state=DemucsSupervisorStepState(),
                    database=MagicMock(),
                    source_client=MagicMock(),
                    artifact_client=MagicMock(),
                    work_directory=Path("/worker-scratch"),
                    shutdown_waiter=MagicMock(),
                )
                self.assertEqual(result.action_result.outcome, outcome)

        self.assertEqual(run_step.call_count, 2)

    @patch("app.runtime.supervisor_once.apply_demucs_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_demucs_supervisor_step")
    def test_step_or_action_failure_propagates_without_constructing_result(self, run_step, apply_action) -> None:
        """The future entrypoint receives original failures, not false continuation."""

        step_failure = RuntimeError("test-only step failure")
        run_step.side_effect = step_failure
        with self.assertRaises(RuntimeError) as raised:
            run_one_demucs_supervisor_cycle(
                MagicMock(),
                state=DemucsSupervisorStepState(),
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=MagicMock(),
            )
        self.assertIs(raised.exception, step_failure)
        apply_action.assert_not_called()

        run_step.reset_mock(return_value=True, side_effect=True)
        run_step.return_value = retryable_step()
        action_failure = RuntimeError("test-only action failure")
        apply_action.side_effect = action_failure
        with self.assertRaises(RuntimeError) as raised:
            run_one_demucs_supervisor_cycle(
                MagicMock(),
                state=DemucsSupervisorStepState(),
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=MagicMock(),
            )
        self.assertIs(raised.exception, action_failure)


class DemucsSupervisorOnceResultTests(unittest.TestCase):
    """Prove a one-cycle result cannot mismatch step decision, action, or state."""

    def test_result_rejects_mismatched_action_or_next_state(self) -> None:
        """A later loop cannot resume with state unrelated to the actioned step."""

        step = retryable_step()
        action = DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.CONTINUE,
            action=DemucsSupervisorAction.RETRY_AFTER_BACKOFF,
        )
        with self.assertRaises(ValueError):
            DemucsSupervisorOnceResult(
                step=step,
                action_result=action,
                next_state=DemucsSupervisorStepState(),
            )


if __name__ == "__main__":
    unittest.main()
