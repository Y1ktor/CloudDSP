"""Unit tests for ADTOF's shutdown-aware persistent supervisor loop.

The one-step runner is mocked at its public seam. The tests use a real
in-memory shutdown event and never connect to services, wait for a timeout, run
ADTOF, build an image, or create Kubernetes resources.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.runtime.recovery_cadence import ADTOFWorkerCadenceAction, ADTOFWorkerCadenceState
from app.runtime.shutdown_event import ADTOFShutdownWaiter
from app.runtime.supervisor_action import ADTOFSupervisorActionOutcome, ADTOFSupervisorActionResult
from app.runtime.supervisor_backoff import (
    ADTOFSupervisorAction,
    ADTOFSupervisorBackoffState,
    ADTOFSupervisorDecision,
    ADTOFSupervisorEvent,
)
from app.runtime.supervisor_loop import ADTOFSupervisorLoopOutcome, run_adtof_supervisor_until_stop
from app.runtime.supervisor_once import ADTOFSupervisorOnceResult
from app.runtime.supervisor_step import ADTOFSupervisorStepResult, ADTOFSupervisorStepState


def state(streak: int = 0) -> ADTOFSupervisorStepState:
    """Return local state with a deterministic recovery-first next action."""

    return ADTOFSupervisorStepState(
        backoff_state=ADTOFSupervisorBackoffState(streak),
        cadence_state=ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN),
    )


def once_result(
    outcome: ADTOFSupervisorActionOutcome,
    *,
    next_state: ADTOFSupervisorStepState,
) -> ADTOFSupervisorOnceResult:
    """Return one valid runner observation with a matching terminal/control action."""

    if outcome is ADTOFSupervisorActionOutcome.EXIT_FATAL:
        event = ADTOFSupervisorEvent.FATAL_CONFIGURATION
        action = ADTOFSupervisorAction.EXIT_FATAL
        delay = 0.0
        decision_state = ADTOFSupervisorBackoffState()
        next_state = ADTOFSupervisorStepState(
            backoff_state=decision_state,
            cadence_state=next_state.cadence_state,
        )
    else:
        event = ADTOFSupervisorEvent.RETRYABLE_FAILURE
        action = ADTOFSupervisorAction.RETRY_AFTER_BACKOFF
        delay = 1.0
        decision_state = next_state.backoff_state

    step = ADTOFSupervisorStepResult(
        event=event,
        decision=ADTOFSupervisorDecision(
            action=action,
            delay_seconds=delay,
            next_state=decision_state,
        ),
        next_state=next_state,
    )
    return ADTOFSupervisorOnceResult(
        step=step,
        action_result=ADTOFSupervisorActionResult(outcome=outcome, action=action),
        next_state=next_state,
    )


class ADTOFSupervisorLoopTests(unittest.TestCase):
    """Prove the only repeated path is a confirmed `continue` action result."""

    @patch("app.runtime.supervisor_loop.run_one_adtof_supervisor_cycle")
    def test_preexisting_shutdown_stops_before_any_worker_cycle(self, run_once) -> None:
        """SIGTERM observed after setup cannot permit a first broker receive."""

        waiter = ADTOFShutdownWaiter()
        waiter.request_shutdown()
        initial = state()

        result = run_adtof_supervisor_until_stop(
            MagicMock(),
            initial_state=initial,
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.final_state, initial)
        self.assertEqual(result.completed_cycles, 0)
        self.assertIsNone(result.final_cycle)
        run_once.assert_not_called()

    @patch("app.runtime.supervisor_loop.run_one_adtof_supervisor_cycle")
    def test_continue_repeats_once_then_shutdown_result_stops_loop(self, run_once) -> None:
        """Only a completed continue action permits exactly one additional cycle."""

        first = once_result(ADTOFSupervisorActionOutcome.CONTINUE, next_state=state(1))
        second = once_result(ADTOFSupervisorActionOutcome.SHUTDOWN_REQUESTED, next_state=state(2))
        run_once.side_effect = [first, second]
        waiter = ADTOFShutdownWaiter()
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()

        result = run_adtof_supervisor_until_stop(
            channel,
            initial_state=state(),
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
            process_timeout_seconds=120,
            process_runner=MagicMock(),
            jitter_fraction=0.25,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_cycles, 2)
        self.assertIs(result.final_cycle, second)
        self.assertEqual(result.final_state, second.next_state)
        self.assertEqual(run_once.call_count, 2)
        self.assertEqual(run_once.call_args_list[1].kwargs["state"], first.next_state)

    @patch("app.runtime.supervisor_loop.run_one_adtof_supervisor_cycle")
    def test_signal_after_continue_stops_before_the_next_worker_cycle(self, run_once) -> None:
        """A SIGTERM during bounded work is observed before another broker poll."""

        waiter = ADTOFShutdownWaiter()
        continued = once_result(ADTOFSupervisorActionOutcome.CONTINUE, next_state=state(1))

        def continue_then_request_shutdown(*_args, **_kwargs):
            waiter.request_shutdown()
            return continued

        run_once.side_effect = continue_then_request_shutdown
        result = run_adtof_supervisor_until_stop(
            MagicMock(),
            initial_state=state(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=waiter,
        )

        self.assertEqual(result.outcome, ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED)
        self.assertEqual(result.completed_cycles, 1)
        self.assertIs(result.final_cycle, continued)
        run_once.assert_called_once()

    @patch("app.runtime.supervisor_loop.run_one_adtof_supervisor_cycle")
    def test_fatal_action_stops_loop_with_its_terminal_state(self, run_once) -> None:
        """Fatal static configuration never creates another worker cycle."""

        fatal = once_result(ADTOFSupervisorActionOutcome.EXIT_FATAL, next_state=state(3))
        run_once.return_value = fatal

        result = run_adtof_supervisor_until_stop(
            MagicMock(),
            initial_state=state(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
            shutdown_waiter=ADTOFShutdownWaiter(),
        )

        self.assertEqual(result.outcome, ADTOFSupervisorLoopOutcome.EXIT_FATAL)
        self.assertEqual(result.completed_cycles, 1)
        self.assertIs(result.final_cycle, fatal)
        run_once.assert_called_once()

    @patch("app.runtime.supervisor_loop.run_one_adtof_supervisor_cycle")
    def test_runner_failure_propagates_without_another_cycle(self, run_once) -> None:
        """Unclassified failure remains visible to the outer lifecycle owner."""

        failure = RuntimeError("private runner failure")
        run_once.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            run_adtof_supervisor_until_stop(
                MagicMock(),
                initial_state=state(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
                shutdown_waiter=ADTOFShutdownWaiter(),
            )
        self.assertIs(raised.exception, failure)
        run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
