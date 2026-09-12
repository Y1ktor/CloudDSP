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
from app.stem_task_terminal_failure import (
    BasicPitchStemTerminalFailure,
    BasicPitchStemTerminalFailureCode,
)
from app.stem_retry_handling import (
    BasicPitchPreModelRetryHandling,
    BasicPitchPreModelRetryHandlingDisposition,
)
from app.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
)
from app.stem_task_retry_schedule import BasicPitchStemRetrySchedule, BasicPitchStemRetryScheduleCode
from app.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


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


def acknowledged_lease(**overrides: object) -> BasicPitchTaskLease:
    """Return the exact committed lease that belongs to ``acknowledged_message``."""

    arguments: dict[str, object] = {
        "task_id": "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/vocals.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": "49d78c86-9591-4fcb-85d8-694c15808a65",
        "lease_expires_at": datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    }
    arguments.update(overrides)
    return BasicPitchTaskLease(**arguments)  # type: ignore[arg-type]


def acknowledged_result(**lease_overrides: object) -> BasicPitchConsumeOneResult:
    """Build the sole receive result that may reach the execution coordinator."""

    return BasicPitchConsumeOneResult(
        outcome=BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE,
        lease=acknowledged_lease(**lease_overrides),
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

    @patch("app.acknowledged_lease_execution.commit_terminal_basic_pitch_stem_failure")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_known_permanent_pre_model_failure_becomes_a_durable_terminal_result(
        self,
        execute,
        classify,
        commit_failure,
    ) -> None:
        """A valid, acknowledged message is not retried or DLQed after this result."""

        database = MagicMock()
        original_error = RuntimeError("private fake permanent verification failure")
        execute.side_effect = original_error
        classify.return_value = BasicPitchStemTerminalFailureCode.METADATA_MISMATCH
        recorded = BasicPitchStemTerminalFailure(
            task_id=acknowledged_lease().task_id,
            job_id=JOB_ID,
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
            completed_at=datetime(2026, 9, 11, 12, 20, tzinfo=UTC),
        )
        commit_failure.return_value = recorded

        returned = execute_acknowledged_basic_pitch_lease(
            acknowledged_result(),
            database=database,
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchClaimedTaskExecutionOutcome.TERMINAL_FAILURE)
        self.assertIs(returned.terminal_failure, recorded)
        self.assertIsNone(returned.completion)
        classify.assert_called_once_with(original_error)
        commit_failure.assert_called_once_with(
            database=database,
            lease=acknowledged_lease(),
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
        )

    @patch("app.acknowledged_lease_execution.commit_terminal_basic_pitch_stem_failure")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_terminal_failure_ownership_loss_stops_without_replacing_newer_state(
        self,
        execute,
        classify,
        commit_failure,
    ) -> None:
        """A stale worker must not fabricate terminal evidence after a race."""

        execute.side_effect = RuntimeError("private fake permanent verification failure")
        classify.return_value = BasicPitchStemTerminalFailureCode.DOWNLOAD_CHECKSUM_MISMATCH
        commit_failure.return_value = None

        returned = execute_acknowledged_basic_pitch_lease(
            acknowledged_result(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST)
        self.assertIsNone(returned.terminal_failure)

    @patch("app.acknowledged_lease_execution.handle_basic_pitch_pre_model_storage_retry")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_unclassified_execution_failure_propagates_without_a_terminal_update(
        self,
        execute,
        classify,
        handle_retry,
    ) -> None:
        """The later supervisor must own retryable and unexpected failures."""

        failure = RuntimeError("private fake execution failure")
        execute.side_effect = failure
        classify.return_value = None
        handle_retry.return_value = BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED,
        )

        with self.assertRaises(RuntimeError) as raised:
            execute_acknowledged_basic_pitch_lease(
                acknowledged_result(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)
        handle_retry.assert_called_once_with(
            failure,
            database=unittest.mock.ANY,
            lease=acknowledged_lease(),
        )

    @patch("app.acknowledged_lease_execution.handle_basic_pitch_pre_model_storage_retry")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_storage_outage_with_remaining_attempts_returns_committed_retry_schedule(
        self,
        execute,
        classify_permanent,
        handle_retry,
    ) -> None:
        """The acknowledged delivery stays consumed while PostgreSQL owns later retry time."""

        database = MagicMock()
        failure = RuntimeError("private safe storage-wrapper failure")
        execute.side_effect = failure
        classify_permanent.return_value = None
        scheduled = BasicPitchStemRetrySchedule(
            task_id=acknowledged_lease().task_id,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            available_at=datetime(2026, 9, 11, 12, 21, tzinfo=UTC),
        )
        handle_retry.return_value = BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED,
            retry_schedule=scheduled,
        )

        returned = execute_acknowledged_basic_pitch_lease(
            acknowledged_result(),
            database=database,
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchClaimedTaskExecutionOutcome.RETRY_SCHEDULED)
        self.assertIs(returned.retry_schedule, scheduled)
        self.assertIsNone(returned.retry_exhaustion)
        handle_retry.assert_called_once_with(failure, database=database, lease=acknowledged_lease())

    @patch("app.acknowledged_lease_execution.handle_basic_pitch_pre_model_storage_retry")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_final_storage_outage_returns_committed_retry_exhaustion(
        self,
        execute,
        classify_permanent,
        handle_retry,
    ) -> None:
        """The third acknowledged outage cannot report a fake later retry time."""

        database = MagicMock()
        failure = RuntimeError("private safe storage-wrapper failure")
        execute.side_effect = failure
        classify_permanent.return_value = None
        exhausted = BasicPitchStemRetryExhaustion(
            task_id=acknowledged_lease().task_id,
            job_id=JOB_ID,
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            completed_at=datetime(2026, 9, 11, 12, 21, tzinfo=UTC),
        )
        handle_retry.return_value = BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED,
            retry_exhaustion=exhausted,
        )

        returned = execute_acknowledged_basic_pitch_lease(
            acknowledged_result(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
            database=database,
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchClaimedTaskExecutionOutcome.RETRY_EXHAUSTED)
        self.assertIsNone(returned.retry_schedule)
        self.assertIs(returned.retry_exhaustion, exhausted)
        handle_retry.assert_called_once_with(
            failure,
            database=database,
            lease=acknowledged_lease(attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS),
        )

    @patch("app.acknowledged_lease_execution.handle_basic_pitch_pre_model_storage_retry")
    @patch("app.acknowledged_lease_execution.classify_basic_pitch_pre_model_terminal_failure")
    @patch("app.acknowledged_lease_execution.execute_claimed_basic_pitch_task")
    def test_retry_guard_miss_becomes_normal_ownership_loss(
        self,
        execute,
        classify_permanent,
        handle_retry,
    ) -> None:
        """A stale worker cannot create retry or exhaustion evidence after a race."""

        execute.side_effect = RuntimeError("private safe storage-wrapper failure")
        classify_permanent.return_value = None
        handle_retry.return_value = BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT,
        )

        returned = execute_acknowledged_basic_pitch_lease(
            acknowledged_result(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST)
        self.assertIsNone(returned.retry_schedule)
        self.assertIsNone(returned.retry_exhaustion)


if __name__ == "__main__":
    unittest.main()
