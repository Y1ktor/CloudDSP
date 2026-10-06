"""Unit tests for ADTOF's pure normal-work/recovery fairness cadence.

The policy handles only immutable iteration facts. It opens no connection,
receives no RabbitMQ message, invokes no model, sleeps, or creates Kubernetes
resources.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.runtime.recovery_cadence import (
    ADTOFWorkerCadenceAction,
    ADTOFWorkerCadenceState,
    advance_after_adtof_normal_iteration,
    advance_after_adtof_recovery_iteration,
    initial_adtof_worker_cadence_state,
)
from app.runtime.recovery_execute_once import ADTOFRecoveryIterationOutcome, ADTOFRecoveryIterationResult
from app.runtime.receive_execute_once import ADTOFWorkerIterationOutcome, ADTOFWorkerIterationResult
from app.db.task_claim import ADTOFExpiredLeaseTerminalization


def execution() -> ADTOFClaimedTaskSuccess:
    """Return compact ownership-loss evidence without an external side effect."""

    return ADTOFClaimedTaskSuccess(ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST)


def terminalization() -> ADTOFExpiredLeaseTerminalization:
    """Return task-only evidence that no fourth recovery lease is possible."""

    return ADTOFExpiredLeaseTerminalization(
        task_id="9381d35a-355f-4fb1-bb39-32ceba7d917f",
        job_id="08ec1d44-3106-4fcb-91c8-5d0c78e7e046",
        stem_name="drums",
        attempt_count=3,
        completed_at=datetime(2026, 9, 14, 12, 30, tzinfo=UTC),
        error_code="lease_expired_attempts_exhausted",
    )


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

        # A terminal third attempt is durable recovery progress, not a failed
        # scan. The cadence must still yield exactly one normal AMQP turn.
        state = advance_after_adtof_normal_iteration(
            state,
            ADTOFWorkerIterationResult(
                ADTOFWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
            ),
        )
        state = advance_after_adtof_recovery_iteration(
            state,
            ADTOFRecoveryIterationResult(
                ADTOFRecoveryIterationOutcome.TERMINALIZED,
                terminalization=terminalization(),
            ),
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
