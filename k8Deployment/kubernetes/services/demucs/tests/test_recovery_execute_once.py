"""Tests for Demucs's bounded delivery-free recovery iteration.

The terminalization, recovery composition, and execution gate are all patched
at public seams. No test connects to PostgreSQL, RabbitMQ, MinIO, Demucs,
Docker, or Kubernetes.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.messaging.demucs_requested_message import DemucsRequestedMessage
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.runtime.recovery_execute_once import (
    DemucsRecoveryIterationOutcome,
    DemucsRecoveryIterationResult,
    recover_and_execute_demucs_once,
)
from app.db.task_lease import DEMUCS_EXHAUSTED_LEASE_ERROR_CODE, DemucsExpiredLeaseTerminalization, DemucsTaskLease
from app.db.task_maintenance import DemucsRecoveredTask


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"


def recovered_task() -> DemucsRecoveredTask:
    """Return one committed retry lease/request pair with no AMQP frame."""

    lease = DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=2,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 21, 12, 15, tzinfo=UTC),
    )
    return DemucsRecoveredTask(
        lease=lease,
        message=DemucsRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            source_bucket="clouddsp-uploads",
            source_object_key=f"uploads/{JOB_ID}/mix.wav",
            stem_mode="4-stems",
        ),
    )


def terminalization() -> DemucsExpiredLeaseTerminalization:
    """Return one durable final-attempt task/Job failure evidence record."""

    return DemucsExpiredLeaseTerminalization(
        task_id=TASK_ID,
        job_id=JOB_ID,
        attempt_count=3,
        completed_at=datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
        job_revision=8,
        error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    )


class RecoveryExecuteOnceTests(unittest.TestCase):
    """Prove this iteration has one safe terminal/recovery branch only."""

    @patch("app.runtime.recovery_execute_once.execute_recovered_demucs_task")
    @patch("app.runtime.recovery_execute_once.recover_one_demucs_task")
    @patch("app.runtime.recovery_execute_once.terminalize_one_expired_exhausted_demucs_task")
    def test_idle_scan_does_not_enter_the_execution_gate(self, terminalize, recover, execute) -> None:
        """No final/reclaimable candidate is normal idle rather than model work."""

        terminalize.return_value = None
        recover.return_value = None
        database = MagicMock()

        result = recover_and_execute_demucs_once(
            database=database,
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/tmp/demucs-test-scratch"),
        )

        self.assertEqual(result, DemucsRecoveryIterationResult(DemucsRecoveryIterationOutcome.IDLE))
        terminalize.assert_called_once_with(database=database)
        recover.assert_called_once_with(database=database)
        execute.assert_not_called()

    @patch("app.runtime.recovery_execute_once.execute_recovered_demucs_task")
    @patch("app.runtime.recovery_execute_once.recover_one_demucs_task")
    @patch("app.runtime.recovery_execute_once.terminalize_one_expired_exhausted_demucs_task")
    def test_committed_pair_executes_once_without_an_amqp_parameter(self, terminalize, recover, execute) -> None:
        """The gate receives the pair and regular dependencies, never a channel."""

        terminalize.return_value = None
        task = recovered_task()
        recover.return_value = task
        execution = DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        execute.return_value = execution
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()
        demucs_runner = MagicMock()

        result = recover_and_execute_demucs_once(
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=Path("/tmp/demucs-test-scratch"),
            demucs_runner=demucs_runner,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

        self.assertEqual(result.outcome, DemucsRecoveryIterationOutcome.EXECUTED)
        self.assertIs(result.execution, execution)
        terminalize.assert_called_once_with(database=database)
        execute.assert_called_once_with(
            task,
            source_client=source_client,
            artifact_client=artifact_client,
            database=database,
            work_directory=Path("/tmp/demucs-test-scratch"),
            ffprobe_runner=None,
            demucs_runner=demucs_runner,
            uploader=None,
            event_id_factory=unittest.mock.ANY,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

    @patch("app.runtime.recovery_execute_once.execute_recovered_demucs_task")
    @patch("app.runtime.recovery_execute_once.recover_one_demucs_task")
    @patch("app.runtime.recovery_execute_once.terminalize_one_expired_exhausted_demucs_task")
    def test_final_attempt_terminalizes_without_claiming_or_executing(self, terminalize, recover, execute) -> None:
        """Terminal durable work has priority over a new recovery lease."""

        evidence = terminalization()
        terminalize.return_value = evidence
        database = MagicMock()

        result = recover_and_execute_demucs_once(
            database=database,
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            work_directory=Path("/tmp/demucs-test-scratch"),
        )

        self.assertEqual(result.outcome, DemucsRecoveryIterationOutcome.TERMINALIZED)
        self.assertIs(result.terminalization, evidence)
        recover.assert_not_called()
        execute.assert_not_called()

    @patch("app.runtime.recovery_execute_once.execute_recovered_demucs_task")
    @patch("app.runtime.recovery_execute_once.recover_one_demucs_task")
    @patch("app.runtime.recovery_execute_once.terminalize_one_expired_exhausted_demucs_task")
    def test_invalid_recovery_or_execution_error_is_not_silently_mapped_to_idle(
        self,
        terminalize,
        recover,
        execute,
    ) -> None:
        """The future supervisor must distinguish outage/bug from normal idle."""

        terminalize.return_value = None
        recover.return_value = object()
        with self.assertRaisesRegex(TypeError, "invalid result"):
            recover_and_execute_demucs_once(
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/tmp/demucs-test-scratch"),
            )
        execute.assert_not_called()

        recover.reset_mock(return_value=True, side_effect=True)
        recover.return_value = recovered_task()
        failure = RuntimeError("test-only execution failure")
        execute.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            recover_and_execute_demucs_once(
                database=MagicMock(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                work_directory=Path("/tmp/demucs-test-scratch"),
            )
        self.assertIs(raised.exception, failure)


class RecoveryIterationResultTests(unittest.TestCase):
    """Prove idle, terminal, and execution evidence cannot be cross-represented."""

    def test_result_requires_exactly_its_matching_evidence(self) -> None:
        """A later supervisor cannot mistake terminalization for model execution."""

        execution = DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        exhausted = terminalization()
        for outcome, paired_execution, paired_terminalization in (
            (DemucsRecoveryIterationOutcome.EXECUTED, None, None),
            (DemucsRecoveryIterationOutcome.TERMINALIZED, None, None),
            (DemucsRecoveryIterationOutcome.IDLE, execution, None),
            (DemucsRecoveryIterationOutcome.IDLE, None, exhausted),
            (DemucsRecoveryIterationOutcome.EXECUTED, execution, exhausted),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    DemucsRecoveryIterationResult(
                        outcome=outcome,
                        execution=paired_execution,
                        terminalization=paired_terminalization,
                    )


if __name__ == "__main__":
    unittest.main()
