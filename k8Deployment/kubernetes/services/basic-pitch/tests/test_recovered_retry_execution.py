"""Unit tests for the recovered-Basic-Pitch-retry execution gate.

The shared execution policy is patched at its public seam. These tests prove
that recovery forwards only its committed lease/request pair and does not make
an AMQP decision or use a storage/model/database client directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.due_retry_recovery import BasicPitchDueRetryRecovery
from app.recovered_retry_execution import (
    BasicPitchRecoveredRetryExecutionError,
    execute_recovered_basic_pitch_retry,
)
from app.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
STEM_KEY = f"stems/{JOB_ID}/vocals.wav"


def recovery() -> BasicPitchDueRetryRecovery:
    """Return one committed second-attempt lease paired with strict evidence."""

    return BasicPitchDueRetryRecovery(
        lease=BasicPitchTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=STEM_KEY,
            stem_mode="4-stems",
            attempt_count=2,
            lease_token=RECOVERY_TOKEN,
            lease_expires_at=datetime(2026, 9, 12, 12, 15, tzinfo=UTC),
        ),
        message=BasicPitchRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            stem_bucket="clouddsp-uploads",
            stem_object_key=STEM_KEY,
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
    )


class RecoveredRetryExecutionTests(unittest.TestCase):
    """Prove retry recovery reaches the same post-lease policy exactly once."""

    @patch("app.recovered_retry_execution.execute_current_basic_pitch_lease")
    def test_committed_pair_forwards_to_shared_policy_without_a_broker_action(self, execute) -> None:
        """The shared policy owns all terminal/retry outcome handling."""

        expected = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute.return_value = expected
        database = MagicMock()
        storage_client = MagicMock()
        runner = MagicMock()
        committed = recovery()

        returned = execute_recovered_basic_pitch_retry(
            committed,
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
            message=committed.message,
            lease=committed.lease,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=runner,
        )

    @patch("app.recovered_retry_execution.execute_current_basic_pitch_lease")
    def test_noncommitted_or_missing_pair_stops_before_shared_policy(self, execute) -> None:
        """An old RabbitMQ body cannot be substituted for recovery evidence."""

        with self.assertRaises(BasicPitchRecoveredRetryExecutionError):
            execute_recovered_basic_pitch_retry(
                object(),  # type: ignore[arg-type]
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
