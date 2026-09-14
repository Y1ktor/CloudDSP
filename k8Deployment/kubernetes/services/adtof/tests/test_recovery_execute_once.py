"""Unit tests for ADTOF's bounded delivery-free recovery iteration.

The recovery transaction and execution gate are patched at their public seams.
No test connects to PostgreSQL, MinIO, RabbitMQ, ADTOF, or Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.adtof_requested_message import ADTOFRequestedMessage
from app.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.recovery import ADTOFRecoveredTask
from app.recovery_execute_once import (
    ADTOFRecoveryIterationOutcome,
    ADTOFRecoveryIterationResult,
    recover_and_execute_adtof_once,
)
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def recovered_task() -> ADTOFRecoveredTask:
    """Return one previously committed lease/outbox-evidence pair."""

    return ADTOFRecoveredTask(
        lease=ADTOFTaskLease(
            task_id="9381d35a-355f-4fb1-bb39-32ceba7d917f",
            job_id=JOB_ID,
            stem_name="drums",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_mode="4-stems",
            attempt_count=2,
            lease_token="63c9d8d2-11db-41c4-9cc5-79889f912f98",
            lease_expires_at=datetime(2026, 9, 14, 12, 15, tzinfo=UTC),
        ),
        message=ADTOFRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="drums",
            stem_bucket="clouddsp-uploads",
            stem_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_content_length=1234,
            stem_sha256="a" * 64,
        ),
    )


class ADTOFRecoveryExecuteOnceTests(unittest.TestCase):
    """Prove a recovery scan reaches CPU work only through a committed pair."""

    @patch("app.recovery_execute_once.execute_recovered_adtof_task")
    @patch("app.recovery_execute_once.recover_one_expired_adtof_task")
    def test_idle_recovery_scan_never_enters_execution_gate(self, recover, execute) -> None:
        """No expired candidate is visible as normal idle, not failed execution."""

        recover.return_value = None
        database = MagicMock()

        result = recover_and_execute_adtof_once(
            database=database,
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(result, ADTOFRecoveryIterationResult(ADTOFRecoveryIterationOutcome.IDLE))
        recover.assert_called_once_with(database=database)
        execute.assert_not_called()

    @patch("app.recovery_execute_once.execute_recovered_adtof_task")
    @patch("app.recovery_execute_once.recover_one_expired_adtof_task")
    def test_committed_pair_executes_once_with_explicit_dependencies(self, recover, execute) -> None:
        """Only the gate receives the recovery pair; no AMQP parameter exists."""

        task = recovered_task()
        recover.return_value = task
        execution = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = execution
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        result = recover_and_execute_adtof_once(
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertEqual(result.outcome, ADTOFRecoveryIterationOutcome.EXECUTED)
        self.assertIs(result.execution, execution)
        execute.assert_called_once_with(
            task,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

    @patch("app.recovery_execute_once.execute_recovered_adtof_task")
    @patch("app.recovery_execute_once.recover_one_expired_adtof_task")
    def test_invalid_or_operational_recovery_never_becomes_idle(self, recover, execute) -> None:
        """A supervisor can distinguish an outage/bug from an empty scan."""

        recover.return_value = object()
        with self.assertRaisesRegex(TypeError, "invalid result"):
            recover_and_execute_adtof_once(
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        execute.assert_not_called()

        recover.reset_mock(return_value=True, side_effect=True)
        recover.return_value = recovered_task()
        failure = RuntimeError("private execution failure")
        execute.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            recover_and_execute_adtof_once(
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, failure)


class ADTOFRecoveryIterationResultTests(unittest.TestCase):
    """Prove idle and execution results cannot be cross-represented."""

    def test_execution_pairing_is_checked_at_result_construction(self) -> None:
        """Only an executed recovery result may retain coordinator evidence."""

        execution = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        for outcome, paired_execution in (
            (ADTOFRecoveryIterationOutcome.EXECUTED, None),
            (ADTOFRecoveryIterationOutcome.IDLE, execution),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    ADTOFRecoveryIterationResult(
                        outcome=outcome,
                        execution=paired_execution,
                    )


if __name__ == "__main__":
    unittest.main()
