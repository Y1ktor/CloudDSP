"""Unit tests for the acknowledged-lease-to-Basic-Pitch-execution handoff.

The execution coordinator is patched at its public boundary. These tests prove
that only a broker-acknowledged lease can enter MinIO/model work; they do not
contact RabbitMQ, PostgreSQL, MinIO, Basic Pitch, Docker, or Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.acknowledged_lease_execution import (
    BasicPitchAcknowledgedLeaseExecutionError,
    execute_acknowledged_basic_pitch_lease,
)
from app.amqp_manual_ack import BasicPitchConsumeOneOutcome, BasicPitchConsumeOneResult
from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.task_lease import BasicPitchTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"


def acknowledged_message() -> BasicPitchRequestedMessage:
    """Return parser-validated evidence paired with an acknowledged lease."""

    return BasicPitchRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_content_length=101,
        stem_sha256="a" * 64,
    )


def acknowledged_lease() -> BasicPitchTaskLease:
    """Return the exact committed lease that belongs to ``acknowledged_message``."""

    return BasicPitchTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        stem_name="vocals",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    )


def acknowledged_result() -> BasicPitchConsumeOneResult:
    """Build the sole receive result that may reach the execution coordinator."""

    return BasicPitchConsumeOneResult(
        outcome=BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=acknowledged_lease(),
        message=acknowledged_message(),
    )


class BasicPitchAcknowledgedLeaseExecutionTests(unittest.TestCase):
    """Prove post-ack execution has exactly one permitted entry condition."""

    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_acknowledged_lease_forwards_exact_validated_evidence_to_coordinator(self, execute) -> None:
        """The coordinator receives the same durable token and message provenance."""

        result = acknowledged_result()
        expected = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = expected
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        returned = execute_acknowledged_basic_pitch_lease(
            result,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertIs(returned, expected)
        execute.assert_called_once_with(
            database=database,
            storage_client=storage_client,
            message=acknowledged_message(),
            lease=acknowledged_lease(),
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_all_other_normal_receive_outcomes_stop_before_execution(self, execute) -> None:
        """Idle, no-work, and DLQ outcomes cannot replay historical audio work."""

        for outcome in (
            BasicPitchConsumeOneOutcome.IDLE,
            BasicPitchConsumeOneOutcome.ACKNOWLEDGED_NO_WORK,
            BasicPitchConsumeOneOutcome.MALFORMED_REJECTED,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(BasicPitchAcknowledgedLeaseExecutionError) as raised:
                    execute_acknowledged_basic_pitch_lease(
                        BasicPitchConsumeOneResult(outcome=outcome),
                        database=MagicMock(),
                        storage_client=MagicMock(),
                        work_directory=Path("/worker-scratch"),
                    )
                self.assertEqual(
                    str(raised.exception),
                    "Basic Pitch execution requires an acknowledged task lease.",
                )

        execute.assert_not_called()

    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_execution_failure_propagates_without_new_broker_or_retry_policy(self, execute) -> None:
        """The later supervisor must classify coordinator failures without hiding them."""

        failure = RuntimeError("private fake execution failure")
        execute.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            execute_acknowledged_basic_pitch_lease(
                acknowledged_result(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
