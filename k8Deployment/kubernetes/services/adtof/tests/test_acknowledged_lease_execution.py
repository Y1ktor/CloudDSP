"""Unit tests for ADTOF's acknowledged-lease execution handoff.

The post-claim coordinator is patched at its public boundary.  These tests do
not contact RabbitMQ, PostgreSQL, MinIO, run ADTOF, build an image, or create
Kubernetes resources.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.acknowledged_lease_execution import (
    ADTOFAcknowledgedLeaseExecutionError,
    execute_acknowledged_adtof_lease,
)
from app.adtof_requested_message import ADTOFRequestedMessage
from app.amqp_manual_ack import ADTOFConsumeOneOutcome, ADTOFConsumeOneResult
from app.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def acknowledged_message() -> ADTOFRequestedMessage:
    """Return strict parser-shaped drums evidence for one acknowledged lease."""

    return ADTOFRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="drums",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_content_length=101,
        stem_sha256="a" * 64,
    )


def acknowledged_lease() -> ADTOFTaskLease:
    """Return the committed task ownership paired with ``acknowledged_message``."""

    return ADTOFTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        stem_name="drums",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 14, 12, 15, tzinfo=UTC),
    )


def acknowledged_result() -> ADTOFConsumeOneResult:
    """Build the only receive result permitted to reach ADTOF CPU work."""

    return ADTOFConsumeOneResult(
        outcome=ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=acknowledged_lease(),
        message=acknowledged_message(),
    )


class ADTOFAcknowledgedLeaseExecutionTests(unittest.TestCase):
    """Prove only a completed broker acknowledgement opens model work."""

    @patch("app.acknowledged_lease_execution.execute_claimed_adtof_task_success_path")
    def test_forwards_exact_acknowledged_evidence_to_success_coordinator(self, execute) -> None:
        """No altered task, stem, or lease token may enter the success path."""

        expected = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = expected
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        returned = execute_acknowledged_adtof_lease(
            acknowledged_result(),
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

    @patch("app.acknowledged_lease_execution.execute_claimed_adtof_task_success_path")
    def test_normal_nonlease_receive_outcomes_stop_before_model_work(self, execute) -> None:
        """Idle, duplicate/stale, and malformed deliveries cannot replay audio."""

        for outcome in (
            ADTOFConsumeOneOutcome.IDLE,
            ADTOFConsumeOneOutcome.ACKNOWLEDGED_NO_WORK,
            ADTOFConsumeOneOutcome.MALFORMED_REJECTED,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ADTOFAcknowledgedLeaseExecutionError) as raised:
                    execute_acknowledged_adtof_lease(
                        ADTOFConsumeOneResult(outcome=outcome),
                        database=MagicMock(),
                        storage_client=MagicMock(),
                        work_directory=Path("/worker-scratch"),
                    )
                self.assertEqual(
                    str(raised.exception),
                    "ADTOF execution requires an acknowledged task lease.",
                )

        execute.assert_not_called()

    @patch("app.acknowledged_lease_execution.execute_claimed_adtof_task_success_path")
    def test_forged_acknowledged_result_stops_before_model_work(self, execute) -> None:
        """The gate repeats type checks beyond the transport result constructor."""

        forged = object.__new__(ADTOFConsumeOneResult)
        object.__setattr__(forged, "outcome", ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE)
        object.__setattr__(forged, "lease", object())
        object.__setattr__(forged, "message", object())

        with self.assertRaises(ADTOFAcknowledgedLeaseExecutionError) as raised:
            execute_acknowledged_adtof_lease(
                forged,
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertEqual(
            str(raised.exception),
            "ADTOF execution requires an acknowledged task lease.",
        )
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
