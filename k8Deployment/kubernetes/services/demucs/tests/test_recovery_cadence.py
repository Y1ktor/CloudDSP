"""Unit tests for Demucs's pure normal-work/recovery fairness cadence.

The policy consumes immutable iteration facts only. It opens no connection,
receives no RabbitMQ message, invokes no model, sleeps, or creates a
Kubernetes resource.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.receive_execute_once import DemucsWorkerIterationOutcome, DemucsWorkerIterationResult
from app.recovery_cadence import (
    DemucsWorkerCadenceAction,
    DemucsWorkerCadenceState,
    advance_after_demucs_normal_iteration,
    advance_after_demucs_recovery_iteration,
    initial_demucs_worker_cadence_state,
)
from app.recovery_execute_once import DemucsRecoveryIterationOutcome, DemucsRecoveryIterationResult
from app.task_lease import DEMUCS_EXHAUSTED_LEASE_ERROR_CODE, DemucsExpiredLeaseTerminalization


def execution() -> DemucsOneTaskExecution:
    """Return compact ownership-loss evidence without external work."""

    return DemucsOneTaskExecution(DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)


def terminalization() -> DemucsExpiredLeaseTerminalization:
    """Return task-only proof that an expired third attempt cannot get a fourth."""

    return DemucsExpiredLeaseTerminalization(
        task_id="00000000-0000-4000-8000-000000000001",
        job_id="00000000-0000-4000-8000-000000000002",
        attempt_count=3,
        completed_at=datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
        job_revision=8,
        error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    )


class DemucsRecoveryCadenceTests(unittest.TestCase):
    """Prove continuous ordinary messages cannot starve crash recovery."""

    def test_new_pod_scans_recovery_before_normal_amqp_work(self) -> None:
        """A restart checks active expired leases before it consumes new requests."""

        self.assertEqual(
            initial_demucs_worker_cadence_state().next_action,
            DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN,
        )

    def test_cadence_strictly_alternates_one_recovery_and_one_normal_iteration(self) -> None:
        """Even a model-running normal task permits another recovery scan next."""

        state = initial_demucs_worker_cadence_state()
        state = advance_after_demucs_recovery_iteration(
            state,
            DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE),
        )
        self.assertEqual(state.next_action, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)

        state = advance_after_demucs_normal_iteration(
            state,
            DemucsWorkerIterationResult(DemucsWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK),
        )
        state = advance_after_demucs_recovery_iteration(
            state,
            DemucsRecoveryIterationResult(
                DemucsRecoveryIterationOutcome.TERMINALIZED,
                terminalization=terminalization(),
            ),
        )
        self.assertEqual(state.next_action, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)

        state = advance_after_demucs_normal_iteration(
            state,
            DemucsWorkerIterationResult(
                DemucsWorkerIterationOutcome.EXECUTED,
                execution=execution(),
            ),
        )
        self.assertEqual(state.next_action, DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)

        state = advance_after_demucs_recovery_iteration(
            state,
            DemucsRecoveryIterationResult(
                DemucsRecoveryIterationOutcome.EXECUTED,
                execution=execution(),
            ),
        )
        self.assertEqual(state.next_action, DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION)

    def test_every_normal_outcome_schedules_recovery_not_just_queue_idle(self) -> None:
        """Duplicate or malformed queue traffic cannot defer recovery indefinitely."""

        for outcome, maybe_execution in (
            (DemucsWorkerIterationOutcome.IDLE, None),
            (DemucsWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK, None),
            (DemucsWorkerIterationOutcome.MALFORMED_REJECTED, None),
            (DemucsWorkerIterationOutcome.EXECUTED, execution()),
        ):
            with self.subTest(outcome=outcome):
                next_state = advance_after_demucs_normal_iteration(
                    DemucsWorkerCadenceState(DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION),
                    DemucsWorkerIterationResult(outcome, execution=maybe_execution),
                )
                self.assertEqual(next_state.next_action, DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN)

    def test_out_of_order_or_forged_iteration_facts_are_rejected(self) -> None:
        """A future supervisor cannot advance by inventing a finished action."""

        with self.assertRaisesRegex(ValueError, "out of order"):
            advance_after_demucs_normal_iteration(
                initial_demucs_worker_cadence_state(),
                DemucsWorkerIterationResult(DemucsWorkerIterationOutcome.IDLE),
            )

        forged = object.__new__(DemucsRecoveryIterationResult)
        object.__setattr__(forged, "outcome", object())
        object.__setattr__(forged, "execution", None)
        object.__setattr__(forged, "terminalization", None)
        with self.assertRaisesRegex(TypeError, "invalid"):
            advance_after_demucs_recovery_iteration(
                initial_demucs_worker_cadence_state(),
                forged,
            )


if __name__ == "__main__":
    unittest.main()
