"""Unit tests for one fair Basic Pitch broker/due-retry source iteration.

Leaf boundaries are patched at their public seams. These tests do not poll a
broker, open PostgreSQL, call MinIO, run Basic Pitch, sleep, build an image, or
change Kubernetes; they prove only source order and compact normal results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.basic_pitch_task_execution import (
    BasicPitchClaimedTaskExecution,
    BasicPitchClaimedTaskExecutionOutcome,
)
from app.due_retry_recovery import BasicPitchDueRetryRecovery
from app.receive_execute_once import BasicPitchWorkerIterationOutcome, BasicPitchWorkerIterationResult
from app.task_lease import BasicPitchTaskLease
from app.work_schedule import BasicPitchWorkScheduleState, BasicPitchWorkSource
from app.work_source_iteration import (
    BasicPitchFairWorkIterationOutcome,
    BasicPitchFairWorkIterationResult,
    run_one_fair_basic_pitch_work_iteration,
)


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"


def recovered_pair() -> BasicPitchDueRetryRecovery:
    """Return valid committed evidence for a patched due-retry recovery call."""

    from app.basic_pitch_requested_message import BasicPitchRequestedMessage

    key = f"stems/{JOB_ID}/vocals.wav"
    return BasicPitchDueRetryRecovery(
        lease=BasicPitchTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=key,
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
            stem_object_key=key,
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
    )


class FairWorkSourceIterationTests(unittest.TestCase):
    """Prove one source cannot starve the other or mask two-source idleness."""

    @patch("app.work_source_iteration.execute_recovered_basic_pitch_retry")
    @patch("app.work_source_iteration.recover_one_due_basic_pitch_retry")
    @patch("app.work_source_iteration.receive_and_execute_basic_pitch_once")
    def test_broker_progress_skips_recovery_until_the_next_fair_iteration(
        self, receive, recover, execute_recovery
    ) -> None:
        """A normal broker delivery is progress even when it is duplicate history."""

        broker_result = BasicPitchWorkerIterationResult(
            outcome=BasicPitchWorkerIterationOutcome.ACKNOWLEDGED_NO_WORK,
        )
        receive.return_value = broker_result

        returned = run_one_fair_basic_pitch_work_iteration(
            MagicMock(),
            schedule_state=BasicPitchWorkScheduleState(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchFairWorkIterationOutcome.PROGRESS)
        self.assertEqual(returned.attempted_sources, (BasicPitchWorkSource.RABBITMQ_DELIVERY,))
        self.assertIs(returned.broker_result, broker_result)
        self.assertIsNone(returned.recovery_execution)
        self.assertEqual(returned.next_state.next_source, BasicPitchWorkSource.DUE_RETRY_RECOVERY)
        recover.assert_not_called()
        execute_recovery.assert_not_called()

    @patch("app.work_source_iteration.execute_recovered_basic_pitch_retry")
    @patch("app.work_source_iteration.recover_one_due_basic_pitch_retry")
    @patch("app.work_source_iteration.receive_and_execute_basic_pitch_once")
    def test_broker_idle_immediately_falls_back_to_due_recovery(
        self, receive, recover, execute_recovery
    ) -> None:
        """A due task is not delayed by a one-second empty-broker wait."""

        receive.return_value = BasicPitchWorkerIterationResult(
            outcome=BasicPitchWorkerIterationOutcome.IDLE,
        )
        pair = recovered_pair()
        recover.return_value = pair
        recovered_execution = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute_recovery.return_value = recovered_execution
        database = MagicMock()
        storage_client = MagicMock()

        returned = run_one_fair_basic_pitch_work_iteration(
            MagicMock(),
            schedule_state=BasicPitchWorkScheduleState(),
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
        )

        self.assertEqual(returned.outcome, BasicPitchFairWorkIterationOutcome.PROGRESS)
        self.assertEqual(
            returned.attempted_sources,
            (BasicPitchWorkSource.RABBITMQ_DELIVERY, BasicPitchWorkSource.DUE_RETRY_RECOVERY),
        )
        self.assertIs(returned.recovery_execution, recovered_execution)
        self.assertEqual(returned.next_state.next_source, BasicPitchWorkSource.RABBITMQ_DELIVERY)
        recover.assert_called_once_with(database=database)
        execute_recovery.assert_called_once_with(
            pair,
            database=database,
            storage_client=storage_client,
            work_directory=Path("/worker-scratch"),
            process_timeout_seconds=120,
            process_runner=None,
        )

    @patch("app.work_source_iteration.execute_recovered_basic_pitch_retry")
    @patch("app.work_source_iteration.recover_one_due_basic_pitch_retry")
    @patch("app.work_source_iteration.receive_and_execute_basic_pitch_once")
    def test_only_two_empty_source_checks_produce_idle(
        self, receive, recover, execute_recovery
    ) -> None:
        """The existing supervisor may wait only after both sources are empty."""

        receive.return_value = BasicPitchWorkerIterationResult(
            outcome=BasicPitchWorkerIterationOutcome.IDLE,
        )
        recover.return_value = None

        returned = run_one_fair_basic_pitch_work_iteration(
            MagicMock(),
            schedule_state=BasicPitchWorkScheduleState(),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchFairWorkIterationOutcome.IDLE)
        self.assertEqual(
            returned.attempted_sources,
            (BasicPitchWorkSource.RABBITMQ_DELIVERY, BasicPitchWorkSource.DUE_RETRY_RECOVERY),
        )
        self.assertIsNone(returned.broker_result)
        self.assertIsNone(returned.recovery_execution)
        execute_recovery.assert_not_called()

    @patch("app.work_source_iteration.execute_recovered_basic_pitch_retry")
    @patch("app.work_source_iteration.recover_one_due_basic_pitch_retry")
    @patch("app.work_source_iteration.receive_and_execute_basic_pitch_once")
    def test_due_retry_preference_runs_without_polling_broker_first(
        self, receive, recover, execute_recovery
    ) -> None:
        """The round-robin state gives durable retry backlog its fair turn."""

        pair = recovered_pair()
        recover.return_value = pair
        recovered_execution = BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
        execute_recovery.return_value = recovered_execution

        returned = run_one_fair_basic_pitch_work_iteration(
            MagicMock(),
            schedule_state=BasicPitchWorkScheduleState(
                next_source=BasicPitchWorkSource.DUE_RETRY_RECOVERY,
            ),
            database=MagicMock(),
            storage_client=MagicMock(),
            work_directory=Path("/worker-scratch"),
        )

        self.assertEqual(returned.outcome, BasicPitchFairWorkIterationOutcome.PROGRESS)
        self.assertEqual(returned.attempted_sources, (BasicPitchWorkSource.DUE_RETRY_RECOVERY,))
        self.assertEqual(returned.next_state.next_source, BasicPitchWorkSource.RABBITMQ_DELIVERY)
        receive.assert_not_called()

    @patch("app.work_source_iteration.execute_recovered_basic_pitch_retry")
    @patch("app.work_source_iteration.recover_one_due_basic_pitch_retry")
    @patch("app.work_source_iteration.receive_and_execute_basic_pitch_once")
    def test_leaf_exception_propagates_without_a_fake_idle_or_progress_result(
        self, receive, recover, execute_recovery
    ) -> None:
        """The later supervisor owns failure classification/backoff decisions."""

        failure = RuntimeError("private test-only RabbitMQ failure")
        receive.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            run_one_fair_basic_pitch_work_iteration(
                MagicMock(),
                schedule_state=BasicPitchWorkScheduleState(),
                database=MagicMock(),
                storage_client=MagicMock(),
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)
        recover.assert_not_called()
        execute_recovery.assert_not_called()

    def test_direct_result_construction_cannot_call_one_empty_source_idle(self) -> None:
        """A single empty queue check cannot accidentally authorize an idle sleep."""

        with self.assertRaises(ValueError):
            BasicPitchFairWorkIterationResult(
                outcome=BasicPitchFairWorkIterationOutcome.IDLE,
                next_state=BasicPitchWorkScheduleState(),
                attempted_sources=(BasicPitchWorkSource.RABBITMQ_DELIVERY,),
            )


if __name__ == "__main__":
    unittest.main()
