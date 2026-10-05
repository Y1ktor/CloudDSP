"""Unit tests for one bounded Basic Pitch receive-and-execute iteration.

The manual-ack adapter and execution gate are patched at their public seams.
These tests open no RabbitMQ/PostgreSQL/MinIO connection, run no Basic Pitch
process, sleep, or create Docker/Kubernetes state. They prove only the control
flow joining already-tested boundaries.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import BasicPitchConsumeOneOutcome, BasicPitchConsumeOneResult
from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.runtime.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.runtime.receive_execute_once import (
    BasicPitchWorkerIterationOutcome,
    BasicPitchWorkerIterationResult,
    receive_and_execute_basic_pitch_once,
)
from app.db.task_lease import BasicPitchTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"


def acknowledged_result() -> BasicPitchConsumeOneResult:
    """Return the single receive result permitted to enter execution in this test."""

    return BasicPitchConsumeOneResult(
        outcome=BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=BasicPitchTaskLease(
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
        ),
        message=BasicPitchRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            stem_bucket="clouddsp-uploads",
            stem_object_key=f"stems/{JOB_ID}/vocals.wav",
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
    )


class BasicPitchReceiveExecuteOnceTests(unittest.TestCase):
    """Prove only an acknowledged first lease enters the execution gate."""

    @patch("app.runtime.receive_execute_once.execute_acknowledged_basic_pitch_lease")
    @patch("app.runtime.receive_execute_once.consume_one_basic_pitch_requested_delivery")
    def test_acknowledged_lease_executes_once_with_all_explicit_dependencies(
        self,
        receive,
        execute,
    ) -> None:
        """The one-iteration join preserves the manual-ack result and all bounds."""

        receive_result = acknowledged_result()
        receive.return_value = receive_result
        execution = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = execution
        channel = MagicMock()
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()

        result = receive_and_execute_basic_pitch_once(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

        self.assertEqual(result.outcome, BasicPitchWorkerIterationOutcome.EXECUTED)
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

    @patch("app.runtime.receive_execute_once.execute_acknowledged_basic_pitch_lease")
    @patch("app.runtime.receive_execute_once.consume_one_basic_pitch_requested_delivery")
    def test_normal_no_work_outcomes_never_enter_the_execution_gate(self, receive, execute) -> None:
        """Idle, duplicate/stale, and DLQ outcomes cannot initiate media work."""

        expected_outcomes = {
            BasicPitchConsumeOneOutcome.IDLE: BasicPitchWorkerIterationOutcome.IDLE,
            BasicPitchConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: BasicPitchWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
            BasicPitchConsumeOneOutcome.MALFORMED_REJECTED: BasicPitchWorkerIterationOutcome.MALFORMED_REJECTED,
        }
        for receive_outcome, expected_outcome in expected_outcomes.items():
            with self.subTest(receive_outcome=receive_outcome):
                receive.reset_mock(return_value=True, side_effect=True)
                receive.return_value = BasicPitchConsumeOneResult(outcome=receive_outcome)
                result = receive_and_execute_basic_pitch_once(
                    MagicMock(),
                    database=MagicMock(),
                    storage_client=MagicMock(),
                    work_directory=Path("/worker-scratch"),
                )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertIsNone(result.execution)

        execute.assert_not_called()

    @patch("app.runtime.receive_execute_once.execute_acknowledged_basic_pitch_lease")
    @patch("app.runtime.receive_execute_once.consume_one_basic_pitch_requested_delivery")
    def test_receive_or_execution_failure_propagates_without_an_iteration_result(self, receive, execute) -> None:
        """The future supervisor must own reconnect/retry classification and timing."""

        receive_failure = RuntimeError("private receive failure")
        receive.side_effect = receive_failure
        with self.assertRaises(RuntimeError) as raised:
            receive_and_execute_basic_pitch_once(
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
            receive_and_execute_basic_pitch_once(
                MagicMock(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, execution_failure)


class BasicPitchWorkerIterationResultTests(unittest.TestCase):
    """Prove no-work iteration outcomes cannot retain fake execution evidence."""

    def test_execution_pairing_is_checked_at_result_construction(self) -> None:
        """Only the executed outcome may expose a coordinator result."""

        execution = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        for outcome, paired_execution in (
            (BasicPitchWorkerIterationOutcome.EXECUTED, None),
            (BasicPitchWorkerIterationOutcome.IDLE, execution),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    BasicPitchWorkerIterationResult(
                        outcome=outcome,
                        execution=paired_execution,
                    )


if __name__ == "__main__":
    unittest.main()
