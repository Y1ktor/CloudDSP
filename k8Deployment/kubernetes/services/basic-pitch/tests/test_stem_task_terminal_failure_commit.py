"""Unit tests for the Basic Pitch terminal-failure transaction composition.

No database connection, RabbitMQ action, MinIO request, worker loop, model,
container, or Kubernetes resource is created.  The pure SQL adapter is patched
at its public seam so these tests prove only commit-boundary control flow.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch

from app.db.stem_task_terminal_failure import (
    BasicPitchStemTerminalFailure,
    BasicPitchStemTerminalFailureCode,
)
from app.db.stem_task_terminal_failure_commit import commit_terminal_basic_pitch_stem_failure
from app.db.task_lease import BasicPitchTaskLease


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


class BasicPitchStemTerminalFailureCommitTests(unittest.TestCase):
    """Prove evidence returns only after the pure statement's transaction scope."""

    @patch("app.db.stem_task_terminal_failure_commit.fail_leased_basic_pitch_stem_task")
    def test_committed_terminal_failure_returns_after_normal_context_exit(self, fail) -> None:
        """The composition forwards only explicit task/lease/code dependencies."""

        database = RecordingDatabase(cursor=object())
        expected = BasicPitchStemTerminalFailure(
            task_id=TASK_ID,
            job_id=JOB_ID,
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
            completed_at=datetime(2026, 9, 11, 13, 5, tzinfo=UTC),
        )
        fail.return_value = expected

        returned = commit_terminal_basic_pitch_stem_failure(
            database=database,
            lease=lease(),
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
        )

        self.assertIs(returned, expected)
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        fail.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            failure_code=BasicPitchStemTerminalFailureCode.METADATA_MISMATCH,
        )

    @patch("app.db.stem_task_terminal_failure_commit.fail_leased_basic_pitch_stem_task")
    def test_normal_ownership_loss_commits_no_mutation_and_returns_none(self, fail) -> None:
        """A stale worker must not turn its no-row result into a failure retry."""

        database = RecordingDatabase(cursor=object())
        fail.return_value = None

        self.assertIsNone(
            commit_terminal_basic_pitch_stem_failure(
                database=database,
                lease=lease(),
                failure_code=BasicPitchStemTerminalFailureCode.DOWNLOAD_CHECKSUM_MISMATCH,
            )
        )
        self.assertEqual(database.context.exit_arguments, (None, None, None))

    @patch("app.db.stem_task_terminal_failure_commit.fail_leased_basic_pitch_stem_task")
    def test_adapter_error_escapes_so_context_can_roll_back(self, fail) -> None:
        """No failed SQL/protocol result may be mistaken for durable evidence."""

        database = RecordingDatabase(cursor=object())
        failure = RuntimeError("private fake database failure")
        fail.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            commit_terminal_basic_pitch_stem_failure(
                database=database,
                lease=lease(),
                failure_code=BasicPitchStemTerminalFailureCode.OBJECT_MISSING,
            )

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)
        self.assertIs(database.context.exit_arguments[1], failure)


if __name__ == "__main__":
    unittest.main()
