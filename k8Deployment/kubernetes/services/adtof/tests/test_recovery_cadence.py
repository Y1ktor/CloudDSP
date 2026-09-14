"""Unit tests for ADTOF's pure normal-work/recovery fairness cadence.

The policy handles only immutable iteration facts. It opens no connection,
receives no RabbitMQ message, invokes no model, sleeps, or creates Kubernetes
resources.
"""

from __future__ import annotations

import unittest

from app.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.recovery_cadence import (
    ADTOFWorkerCadenceAction,
    ADTOFWorkerCadenceState,
    advance_after_adtof_normal_iteration,
    advance_after_adtof_recovery_iteration,
    initial_adtof_worker_cadence_state,
)
from app.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult


def execution() -> ADTOFClaimedTaskSuccess:
    """Return compact ownership-loss evidence without an external side effect."""

    return ADTOFClaimedTaskSuccess(ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)


class ADTOFRecoveryCadenceTests(unittest.TestCase):
    """Prove recovery cannot be starved by normal ADTOF message traffic."""

    def test_new_pod_scans_recovery_before_normal_amqp_work(self) -> None:
        """A restart sees expired active leases without waiting for queue idle."""

        self.assertEqual(
            initial_adtof_worker_cadence_state().next_action,
            ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN,
        )

    def test_cadence_strictly_alternates_one_recovery_and_one_normal_iteration(self) -> None:
        """Every busy normal task still permits a recovery scan before the next one."""

        state = initial_adtof_worker_cadence_state()
        state = advance_after_adtof_recovery_iteration(
            state,
            ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE),
        )
        self.assertEqual(state.next_action, ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)

        state = advance_after_adtof_normal_iteration(
            state,
            ADTOFWorkerIterationResult(
                ADTOFWorkerIterationOutcome.EXECUTED,
                execution=execution(),
            ),
        )
        self.assertEqual(state.next_action, ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)

        state = advance_after_adtof_recovery_iteration(
            state,
            ADTOFRecoveryIterationResult(
                ADTOFRecoveryIterationOutcome.EXECUTED,
                execution=execution(),
            ),
        )
        self.assertEqual(state.next_action, ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION)

    def test_every_normal_outcome_schedules_recovery_not_just_queue_idle(self) -> None:
        """Malformed or duplicate traffic cannot prevent future crash recovery."""

        for outcome, maybe_execution in (
            (ADTOFWorkerIterationOutcome.IDLE, None),
            (ADTOFWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK, None),
            (ADTOFWorkerIterationOutcome.MALFORMED_REJECTED, None),
            (ADTOFWorkerIterationOutcome.EXECUTED, execution()),
        ):
            with self.subTest(outcome=outcome):
                next_state = advance_after_adtof_normal_iteration(
                    ADTOFWorkerCadenceState(ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION),
                    ADTOFWorkerIterationResult(outcome, execution=maybe_execution),
                )
                self.assertEqual(next_state.next_action, ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN)

    def test_out_of_order_or_forged_iteration_facts_are_rejected(self) -> None:
        """A future loop cannot advance by lying about a completed bounded action."""

        with self.assertRaisesRegex(ValueError, "out of order"):
            advance_after_adtof_normal_iteration(
                initial_adtof_worker_cadence_state(),
                ADTOFWorkerIterationResult(ADTOFWorkerIterationOutcome.IDLE),
            )

        forged = object.__new__(ADTOFRecoveryIterationResult)
        object.__setattr__(forged, "outcome", object())
        object.__setattr__(forged, "execution", None)
        with self.assertRaisesRegex(TypeError, "invalid"):
            advance_after_adtof_recovery_iteration(
                initial_adtof_worker_cadence_state(),
                forged,
            )


if __name__ == "__main__":
    unittest.main()
