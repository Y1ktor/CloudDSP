"""Tests for the recovered-task gate into the shared Demucs attempt policy.

All dependencies are patched at the existing public runtime seam. These tests
do not connect to PostgreSQL, RabbitMQ, MinIO, Demucs, Docker, or Kubernetes.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.messaging.demucs_requested_message import DemucsRequestedMessage
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.runtime.recovered_task_execution import (
    DemucsRecoveredTaskExecutionError,
    execute_recovered_demucs_task,
)
from app.db.task_lease import DemucsTaskLease
from app.db.task_maintenance import DemucsRecoveredTask


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"


def recovered_task() -> DemucsRecoveredTask:
    """Return one valid committed pair without an AMQP delivery object."""

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
    message = DemucsRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        source_bucket="clouddsp-uploads",
        source_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
    )
    return DemucsRecoveredTask(lease=lease, message=message)


class RecoveredTaskExecutionTests(unittest.TestCase):
    """Prove recovery reuses the task policy without making a broker action."""

    @patch("app.runtime.recovered_task_execution.execute_acknowledged_demucs_task_with_running_failure_policy")
    def test_valid_pair_forwards_only_its_lease_through_the_compatibility_envelope(self, execute) -> None:
        """The ordinary runtime sees the same current lease, not a broker frame."""

        expected = DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        execute.return_value = expected
        recovery = recovered_task()

        returned = execute_recovered_demucs_task(
            recovery,
            source_client=MagicMock(),
            artifact_client=MagicMock(),
            database=MagicMock(),
            work_directory=Path("/tmp/demucs-test-scratch"),
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

        self.assertIs(returned, expected)
        _args, kwargs = execute.call_args
        carrier = kwargs["receive_result"]
        self.assertIsInstance(carrier, DemucsConsumeOneResult)
        assert isinstance(carrier, DemucsConsumeOneResult)
        self.assertEqual(carrier.outcome, DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE)
        self.assertEqual(carrier.lease, recovery.lease)
        self.assertEqual(kwargs["pre_model_retry_after_seconds"], 31)
        self.assertEqual(kwargs["running_retry_after_seconds"], 47)
        self.assertNotIn("channel", kwargs)

    @patch("app.runtime.recovered_task_execution.execute_acknowledged_demucs_task_with_running_failure_policy")
    def test_non_pair_input_is_rejected_before_the_shared_runtime(self, execute) -> None:
        """An old message or hand-selected lease cannot enter recovery execution."""

        with self.assertRaises(DemucsRecoveredTaskExecutionError):
            execute_recovered_demucs_task(
                object(),  # type: ignore[arg-type]
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                database=MagicMock(),
                work_directory=Path("/tmp/demucs-test-scratch"),
            )

        execute.assert_not_called()

    @patch("app.runtime.recovered_task_execution.execute_acknowledged_demucs_task_with_running_failure_policy")
    def test_shared_runtime_error_remains_its_original_error(self, execute) -> None:
        """The gate must not misclassify a later storage/model/runtime fault."""

        failure = RuntimeError("test-only shared runtime failure")
        execute.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            execute_recovered_demucs_task(
                recovered_task(),
                source_client=MagicMock(),
                artifact_client=MagicMock(),
                database=MagicMock(),
                work_directory=Path("/tmp/demucs-test-scratch"),
            )

        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
