"""Unit tests for the Basic Pitch retry-schedule transaction composition.

No database connection, RabbitMQ action, MinIO request, worker loop, model,
container, or Kubernetes resource is created. The pure SQL adapter is patched
at its public seam so these tests prove only commit-boundary control flow.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch

from app.stem_task_retry_schedule import (
    DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    BasicPitchStemRetrySchedule,
    BasicPitchStemRetryScheduleCode,
)
from app.stem_task_retry_schedule_commit import commit_basic_pitch_stem_retry_schedule
from app.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def lease() -> BasicPitchTaskLease:
    """Return one valid lease whose task is still in the pre-model phase."""

    return BasicPitchTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 11, 13, 15, tzinfo=UTC),
    )


class RecordingWriteCursor(AbstractContextManager[object]):
    """Model commit/rollback exit behavior without a real database driver."""

    def __init__(self, cursor: object) -> None:
        self.cursor = cursor
        self.exit_arguments: tuple[object, object, object] | None = None

    def __enter__(self) -> object:
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:  # type: ignore[no-untyped-def]
        self.exit_arguments = (exc_type, exc_value, traceback)
        return None


class RecordingDatabase:
    """Supply exactly one short cursor scope and record whether it would commit."""

    def __init__(self, cursor: object) -> None:
        self.context = RecordingWriteCursor(cursor)

    def write_cursor(self) -> RecordingWriteCursor:
        return self.context


class BasicPitchStemRetryScheduleCommitTests(unittest.TestCase):
    """Prove retry evidence returns only after its SQL transaction scope exits."""

    @patch("app.stem_task_retry_schedule_commit.schedule_leased_basic_pitch_stem_retry")
    def test_committed_retry_schedule_returns_after_normal_context_exit(self, schedule) -> None:
        """The composition forwards only explicit database/lease/delay/code inputs."""

        database = RecordingDatabase(cursor=object())
        expected = BasicPitchStemRetrySchedule(
            task_id=TASK_ID,
            job_id=JOB_ID,
            attempt_count=1,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            available_at=datetime(2026, 9, 11, 13, 6, tzinfo=UTC),
        )
        schedule.return_value = expected

        returned = commit_basic_pitch_stem_retry_schedule(
            database=database,
            lease=lease(),
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )

        self.assertIs(returned, expected)
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        schedule.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            retry_after_seconds=DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )

    @patch("app.stem_task_retry_schedule_commit.schedule_leased_basic_pitch_stem_retry")
    def test_no_schedule_commits_normally_without_manufacturing_retry_evidence(self, schedule) -> None:
        """A stale, recovered, or exhausted lease needs no competing worker result."""

        database = RecordingDatabase(cursor=object())
        schedule.return_value = None

        self.assertIsNone(
            commit_basic_pitch_stem_retry_schedule(
                database=database,
                lease=lease(),
                retry_after_seconds=45,
                failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            )
        )
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        schedule.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            retry_after_seconds=45,
            failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
        )

    @patch("app.stem_task_retry_schedule_commit.schedule_leased_basic_pitch_stem_retry")
    def test_adapter_error_escapes_so_context_can_roll_back(self, schedule) -> None:
        """No failed SQL/protocol result may be treated as durable retry evidence."""

        database = RecordingDatabase(cursor=object())
        failure = RuntimeError("private fake database failure")
        schedule.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            commit_basic_pitch_stem_retry_schedule(
                database=database,
                lease=lease(),
                failure_code=BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
            )

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)
        self.assertIs(database.context.exit_arguments[1], failure)


if __name__ == "__main__":
    unittest.main()
