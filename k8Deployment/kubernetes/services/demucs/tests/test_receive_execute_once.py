"""Tests for one bounded Demucs receive-and-execute iteration.

The manual-ack adapter and one-attempt runtime are patched at their public
seams. These tests contact no RabbitMQ/PostgreSQL/MinIO service, run no
FFprobe/Demucs process, sleep, build an image, or modify Kubernetes/KEDA.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.runtime.pre_model_failure_runtime import DemucsOneTaskExecution, DemucsOneTaskExecutionOutcome
from app.runtime.receive_execute_once import (
    DemucsWorkerIterationOutcome,
    DemucsWorkerIterationResult,
    receive_and_execute_demucs_once,
)
from app.db.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"


def acknowledged_result() -> DemucsConsumeOneResult:
    """Return the one manual-ack result allowed to begin source/model work."""

    return DemucsConsumeOneResult(
        outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=DemucsTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"uploads/{JOB_ID}/mix.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=datetime(2026, 9, 20, 12, 15, tzinfo=UTC),
        ),
    )


class DemucsReceiveExecuteOnceTests(unittest.TestCase):
    """Prove processing begins only after a manually acknowledged lease."""

    @patch("app.runtime.receive_execute_once.execute_acknowledged_demucs_task_with_running_failure_policy")
    @patch("app.runtime.receive_execute_once.consume_one_demucs_requested_delivery")
    def test_acknowledged_lease_executes_once_with_explicit_dependencies(
        self,
        receive,
        execute,
    ) -> None:
        """The join preserves all reviewed dependency and retry-delay inputs."""

        receive_result = acknowledged_result()
        receive.return_value = receive_result
        execution = DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        execute.return_value = execution
        channel = MagicMock()
        database = MagicMock()
        source_client = MagicMock()
        artifact_client = MagicMock()
        ffprobe_runner = MagicMock()
        demucs_runner = MagicMock()
        uploader = MagicMock()
        event_id_factory = MagicMock()

        result = receive_and_execute_demucs_once(
            channel,
            database=database,  # type: ignore[arg-type]
            source_client=source_client,  # type: ignore[arg-type]
            artifact_client=artifact_client,  # type: ignore[arg-type]
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=ffprobe_runner,  # type: ignore[arg-type]
            demucs_runner=demucs_runner,  # type: ignore[arg-type]
            uploader=uploader,  # type: ignore[arg-type]
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

        self.assertEqual(result.outcome, DemucsWorkerIterationOutcome.EXECUTED)
        self.assertIs(result.execution, execution)
        receive.assert_called_once_with(channel, database=database)
        execute.assert_called_once_with(
            receive_result=receive_result,
            source_client=source_client,
            artifact_client=artifact_client,
            database=database,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=31,
            running_retry_after_seconds=47,
        )

    @patch("app.runtime.receive_execute_once.execute_acknowledged_demucs_task_with_running_failure_policy")
    @patch("app.runtime.receive_execute_once.consume_one_demucs_requested_delivery")
    def test_normal_no_work_outcomes_never_enter_the_execution_gate(self, receive, execute) -> None:
        """Idle, duplicate/stale, and DLQ outcomes cannot begin source/CPU work."""

        expected_outcomes = {
            DemucsConsumeOneOutcome.IDLE: DemucsWorkerIterationOutcome.IDLE,
            DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK: DemucsWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
            DemucsConsumeOneOutcome.MALFORMED_REJECTED: DemucsWorkerIterationOutcome.MALFORMED_REJECTED,
        }
        for receive_outcome, expected_outcome in expected_outcomes.items():
            with self.subTest(receive_outcome=receive_outcome):
                receive.reset_mock(return_value=True, side_effect=True)
                receive.return_value = DemucsConsumeOneResult(outcome=receive_outcome)

                result = receive_and_execute_demucs_once(
                    MagicMock(),
                    database=MagicMock(),  # type: ignore[arg-type]
                    source_client=MagicMock(),  # type: ignore[arg-type]
                    artifact_client=MagicMock(),  # type: ignore[arg-type]
                    work_directory=Path("/worker-scratch"),
                )

                self.assertEqual(result.outcome, expected_outcome)
                self.assertIsNone(result.execution)

        execute.assert_not_called()

    @patch("app.runtime.receive_execute_once.execute_acknowledged_demucs_task_with_running_failure_policy")
    @patch("app.runtime.receive_execute_once.consume_one_demucs_requested_delivery")
    def test_receive_or_runtime_failure_propagates_without_iteration_result(self, receive, execute) -> None:
        """The later supervisor, rather than this join, owns reconnect/backoff."""

        receive_failure = RuntimeError("private receive failure")
        receive.side_effect = receive_failure
        with self.assertRaises(RuntimeError) as raised:
            receive_and_execute_demucs_once(
                MagicMock(),
                database=MagicMock(),  # type: ignore[arg-type]
                source_client=MagicMock(),  # type: ignore[arg-type]
                artifact_client=MagicMock(),  # type: ignore[arg-type]
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, receive_failure)
        execute.assert_not_called()

        receive.reset_mock(return_value=True, side_effect=True)
        receive.return_value = acknowledged_result()
        runtime_failure = RuntimeError("private runtime failure")
        execute.side_effect = runtime_failure
        with self.assertRaises(RuntimeError) as raised:
            receive_and_execute_demucs_once(
                MagicMock(),
                database=MagicMock(),  # type: ignore[arg-type]
                source_client=MagicMock(),  # type: ignore[arg-type]
                artifact_client=MagicMock(),  # type: ignore[arg-type]
                work_directory=Path("/worker-scratch"),
            )
        self.assertIs(raised.exception, runtime_failure)


class DemucsWorkerIterationResultTests(unittest.TestCase):
    """Prove a broker-only outcome cannot carry a forged task result."""

    def test_execution_pairing_is_checked_at_result_construction(self) -> None:
        """Only the executed outcome may expose one-attempt durable evidence."""

        execution = DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        for outcome, paired_execution in (
            (DemucsWorkerIterationOutcome.EXECUTED, None),
            (DemucsWorkerIterationOutcome.IDLE, execution),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    DemucsWorkerIterationResult(
                        outcome=outcome,
                        execution=paired_execution,
                    )


if __name__ == "__main__":
    unittest.main()
