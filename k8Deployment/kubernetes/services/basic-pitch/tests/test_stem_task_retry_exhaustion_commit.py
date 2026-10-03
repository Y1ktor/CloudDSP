"""Unit tests for the Basic Pitch retry-exhaustion transaction composition.

No database connection, RabbitMQ action, MinIO request, worker loop, model,
container, or Kubernetes resource is created. The pure SQL adapter is patched
at its public seam so these tests prove only commit-boundary control flow.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch

from app.db.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
)
from app.db.stem_task_retry_exhaustion_commit import (
    commit_final_attempt_basic_pitch_stem_retry_exhaustion,
)
from app.db.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def lease() -> BasicPitchTaskLease:
    """Return one valid third-attempt lease in the pre-model phase."""

    return BasicPitchTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_mode="4-stems",
        attempt_count=MAX_BASIC_PITCH_TASK_ATTEMPTS,
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


class BasicPitchStemRetryExhaustionCommitTests(unittest.TestCase):
    """Prove final-attempt evidence returns only after the transaction scope exits."""

    @patch("app.db.stem_task_retry_exhaustion_commit.fail_final_attempt_leased_basic_pitch_stem")
    def test_committed_exhaustion_returns_after_normal_context_exit(self, fail) -> None:
        """The composition forwards only explicit database/lease/code dependencies."""

        database = RecordingDatabase(cursor=object())
        expected = BasicPitchStemRetryExhaustion(
            task_id=TASK_ID,
            job_id=JOB_ID,
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            completed_at=datetime(2026, 9, 11, 13, 5, tzinfo=UTC),
        )
        fail.return_value = expected

        returned = commit_final_attempt_basic_pitch_stem_retry_exhaustion(
            database=database,
            lease=lease(),
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )

        self.assertIs(returned, expected)
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        fail.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )

    @patch("app.db.stem_task_retry_exhaustion_commit.fail_final_attempt_leased_basic_pitch_stem")
    def test_normal_ownership_loss_commits_no_mutation_and_returns_none(self, fail) -> None:
        """A stale final-attempt worker cannot manufacture a terminal record."""

        database = RecordingDatabase(cursor=object())
        fail.return_value = None

        self.assertIsNone(
            commit_final_attempt_basic_pitch_stem_retry_exhaustion(
                database=database,
                lease=lease(),
                failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            )
        )
        self.assertEqual(database.context.exit_arguments, (None, None, None))

    @patch("app.db.stem_task_retry_exhaustion_commit.fail_final_attempt_leased_basic_pitch_stem")
    def test_adapter_error_escapes_so_context_can_roll_back(self, fail) -> None:
        """No failed SQL/protocol result may be treated as durable terminal evidence."""

        database = RecordingDatabase(cursor=object())
        failure = RuntimeError("private fake database failure")
        fail.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            commit_final_attempt_basic_pitch_stem_retry_exhaustion(
                database=database,
                lease=lease(),
                failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
            )

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)
        self.assertIs(database.context.exit_arguments[1], failure)


if __name__ == "__main__":
    unittest.main()
