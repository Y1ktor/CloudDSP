"""Unit tests for one complete ADTOF supervisor step/action composition.

The step and shutdown-aware action are patched at their public seams. These
tests do not open services, wait, run ADTOF, loop, build an image, or change
Kubernetes state.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.runtime.recovery_cadence import ADTOFWorkerCadenceAction, ADTOFWorkerCadenceState
from app.runtime.supervisor_action import ADTOFSupervisorActionOutcome, ADTOFSupervisorActionResult
from app.runtime.supervisor_backoff import (
    ADTOFSupervisorAction,
    ADTOFSupervisorBackoffState,
    ADTOFSupervisorDecision,
    ADTOFSupervisorEvent,
)
from app.runtime.supervisor_once import ADTOFSupervisorOnceResult, run_one_adtof_supervisor_cycle
from app.runtime.supervisor_step import ADTOFSupervisorStepResult, ADTOFSupervisorStepState


def retryable_step() -> ADTOFSupervisorStepResult:
    """Return one complete classified step with no normal worker-cycle fact."""

    next_state = ADTOFSupervisorStepState(
        backoff_state=ADTOFSupervisorBackoffState(retryable_failure_streak=1),
        cadence_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
    )
    return ADTOFSupervisorStepResult(
        event=ADTOFSupervisorEvent.RETRYABLE_FAILURE,
        decision=ADTOFSupervisorDecision(
            action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
            delay_seconds=1.0,
            next_state=next_state.backoff_state,
        ),
        next_state=next_state,
    )


class ADTOFSupervisorOnceTests(unittest.TestCase):
    """Prove every completed step gets exactly its one reviewed action."""

    @patch("app.runtime.supervisor_once.apply_adtof_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_adtof_supervisor_step")
    def test_forwards_step_decision_and_returns_its_next_state(self, run_step, apply_action) -> None:
        """The runner cannot substitute another action or local state after a step."""

        step = retryable_step()
        run_step.return_value = step
        action = ADTOFSupervisorActionResult(
            outcome=ADTOFSupervisorActionOutcome.CONTINUE,
            action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
        )
        apply_action.return_value = action
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()
        waiter = MagicMock()
        runner = MagicMock()
        initial_state = ADTOFSupervisorStepState()

        result = run_one_adtof_supervisor_cycle(
            channel,
            state=initial_state,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
            process_timeout_seconds=120,
            process_runner=runner,
            jitter_fraction=0.25,
        )

        self.assertIs(result.step, step)
        self.assertIs(result.action_result, action)
        self.assertEqual(result.next_state, step.next_state)
        run_step.assert_called_once_with(
            channel,
            state=initial_state,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
            jitter_fraction=0.25,
        )
        apply_action.assert_called_once_with(step.decision, shutdown_waiter=waiter)

    @patch("app.runtime.supervisor_once.apply_adtof_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_adtof_supervisor_step")
    def test_shutdown_or_continue_outcome_is_returned_without_another_step(self, run_step, apply_action) -> None:
        """A future entrypoint can stop or continue after this one observed action result."""

        step = retryable_step()
        run_step.return_value = step
        for outcome in (
            ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED,
            ADTOFSupervisorActionOutcome.CONTINUE,
        ):
            with self.subTest(outcome=outcome):
                apply_action.reset_mock(return_value=True, side_effect=True)
                apply_action.return_value = ADTOFSupervisorActionResult(
                    outcome=outcome,
                    action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
                )
                result = run_one_adtof_supervisor_cycle(
                    MagicMock(),
                    state=ADTOFSupervisorStepState(),
                    database=MagicMock(),
                    storage_client=MagicMock(),
                    work_directory=Path("/worker-scratch"),
                    shutdown_waiter=MagicMock(),
                )
                self.assertEqual(result.action_result.outcome, outcome)

        self.assertEqual(run_step.call_count, 2)

    @patch("app.runtime.supervisor_once.apply_adtof_supervisor_decision")
    @patch("app.runtime.supervisor_once.run_one_adtof_supervisor_step")
    def test_step_or_action_failure_propagates_without_constructing_result(self, run_step, apply_action) -> None:
        """A later entrypoint sees the original failure rather than false continuation."""

        step_failure = RuntimeError("private step failure")
        run_step.side_effect = step_failure
        with self.assertRaises(RuntimeError) as raised:
            run_one_adtof_supervisor_cycle(
                MagicMock(),
                state=ADTOFSupervisorStepState(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=MagicMock(),
            )
        self.assertIs(raised.exception, step_failure)
        apply_action.assert_not_called()

        run_step.reset_mock(return_value=True, side_effect=True)
        run_step.return_value = retryable_step()
        action_failure = RuntimeError("private action failure")
        apply_action.side_effect = action_failure
        with self.assertRaises(RuntimeError) as raised:
            run_one_adtof_supervisor_cycle(
                MagicMock(),
                state=ADTOFSupervisorStepState(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=MagicMock(),
            )
        self.assertIs(raised.exception, action_failure)


class ADTOFSupervisorOnceResultTests(unittest.TestCase):
    """Prove a one-cycle result cannot mismatch step decision, action, or state."""

    def test_result_rejects_mismatched_action_or_next_state(self) -> None:
        """A loop must not resume with state unrelated to the applied step."""

        step = retryable_step()
        action = ADTOFSupervisorActionResult(
            outcome=ADTOFSupervisorActionOutcome.CONTINUE,
            action=ADTOFSupervisorAction.RETRY_AFTER_BACKOFF,
        )
        with self.assertRaises(ValueError):
            ADTOFSupervisorOnceResult(
                step=step,
                action_result=action,
                next_state=ADTOFSupervisorStepState(),
            )


if __name__ == "__main__":
    unittest.main()
