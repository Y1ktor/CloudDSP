"""Unit tests for one bounded ADTOF receive-and-execute iteration.

The manual-ack adapter and acknowledged-lease gate are patched at their public
seams. Tests open no RabbitMQ/PostgreSQL/MinIO connection, invoke no ADTOF
process, sleep, build an image, or create Kubernetes state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.messaging.adtof_requested_message import ADTOFRequestedMessage
from app.messaging.amqp_manual_ack import ADTOFConsumeOneOutcome, ADTOFConsumeOneResult
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccess, ADTOFClaimedTaskSuccessOutcome
from app.runtime.receive_execute_once import (
    ADTOFWorkerIterationOutcome,
    ADTOFWorkerIterationResult,
    receive_and_execute_adtof_once,
)
from app.db.task_claim import ADTOFTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"


def acknowledged_result() -> ADTOFConsumeOneResult:
    """Build the sole receive result that may reach the ADTOF execution gate."""

    return ADTOFConsumeOneResult(
        outcome=ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=ADTOFTaskLease(
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
        ),
        message=ADTOFRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="drums",
            stem_bucket="clouddsp-uploads",
            stem_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
    )


class ADTOFReceiveExecuteOnceTests(unittest.TestCase):
    """Prove exactly one acknowledged current lease can enter ADTOF execution."""

    @patch("app.runtime.receive_execute_once.execute_acknowledged_adtof_lease")
    @patch("app.runtime.receive_execute_once.consume_one_adtof_requested_delivery")
    def test_acknowledged_lease_executes_once_with_explicit_dependencies(self, receive, execute) -> None:
        """The join retains all bounds while leaving broker action upstream."""

        receive_result = acknowledged_result()
        receive.return_value = receive_result
        execution = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = execution
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        result = receive_and_execute_adtof_once(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertEqual(result.outcome, ADTOFWorkerIterationOutcome.EXECUTED)
        self.assertIs(result.execution, execution)
        receive.assert_called_once_with(channel, database=database)
        execute.assert_called_once_with(
            receive_result,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

    @patch("app.runtime.receive_execute_once.execute_acknowledged_adtof_lease")
    @patch("app.runtime.receive_execute_once.consume_one_adtof_requested_delivery")
    def test_normal_no_work_outcomes_never_enter_execution_gate(self, receive, execute) -> None:
        """Idle, duplicate/stale, and malformed-DLQ outcomes cannot run CPU work."""

        expected_outcomes = {
            ADTOFConsumeOneOutcome.IDLE: ADTOFWorkerIterationOutcome.IDLE,
            ADTOFConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: ADTOFWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
            ADTOFConsumeOneOutcome.MALFORMED_REJECTED: ADTOFWorkerIterationOutcome.MALFORMED_REJECTED,
        }
        for receive_outcome, expected_outcome in expected_outcomes.items():
            with self.subTest(receive_outcome=receive_outcome):
                receive.reset_mock(return_value=True, side_effect=True)
                receive.return_value = ADTOFConsumeOneResult(outcome=receive_outcome)
                result = receive_and_execute_adtof_once(
                    MagicMock(),
                    database=MagicMock(),
                    storage_client=MagicMock(),
                    work_directory=Path("/worker-scratch"),
                )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertIsNone(result.execution)

        execute.assert_not_called()

    @patch("app.runtime.receive_execute_once.execute_acknowledged_adtof_lease")
    @patch("app.runtime.receive_execute_once.consume_one_adtof_requested_delivery")
    def test_receive_or_execution_failure_propagates_without_iteration_result(self, receive, execute) -> None:
        """A later supervisor owns reconnect, retry, and restart policy."""

        receive_failure = RuntimeError("private receive failure")
        receive.side_effect = receive_failure
        with self.assertRaises(RuntimeError) as raised:
            receive_and_execute_adtof_once(
                MagicMock(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, receive_failure)
        execute.assert_not_called()

        receive.reset_mock(return_value=True, side_effect=True)
        receive.return_value = acknowledged_result()
        execution_failure = RuntimeError("private execution failure")
        execute.side_effect = execution_failure
        with self.assertRaises(RuntimeError) as raised:
            receive_and_execute_adtof_once(
                MagicMock(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, execution_failure)


class ADTOFWorkerIterationResultTests(unittest.TestCase):
    """Prove no-work iterations cannot be forged into an execution result."""

    def test_execution_pairing_is_checked_at_result_construction(self) -> None:
        """Only `EXECUTED` may carry post-claim coordinator evidence."""

        execution = ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
        for outcome, paired_execution in (
            (ADTOFWorkerIterationOutcome.EXECUTED, None),
            (ADTOFWorkerIterationOutcome.IDLE, execution),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    ADTOFWorkerIterationResult(
                        outcome=outcome,
                        execution=paired_execution,
                    )


if __name__ == "__main__":
    unittest.main()
