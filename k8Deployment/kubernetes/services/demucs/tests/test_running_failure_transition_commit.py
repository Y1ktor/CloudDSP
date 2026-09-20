"""Tests for the short Demucs running-failure transaction composition.

The guarded SQL adapter is patched at its public seam. The tests prove only
commit/rollback scope behavior; they open no PostgreSQL connection, use no
MinIO/RabbitMQ/model process, and make no Kubernetes/KEDA change.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch

from app.running_failure_classification import (
    DemucsRunningFailureClassification,
    DemucsRunningFailureDisposition,
    DemucsRunningRetryCode,
)
from app.running_failure_transition import (
    DemucsRunningFailureTransition,
    DemucsRunningFailureTransitionDisposition,
    DemucsRunningRetrySchedule,
)
from app.running_failure_transition_commit import commit_running_demucs_failure_transition
from app.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"


class RecordingWriteCursor(AbstractContextManager[object]):
    """Record how the fake transaction context exits without a database."""

    def __init__(self, cursor: object) -> None:
        """Keep the one inert cursor object passed to the patched SQL adapter."""

        self.cursor = cursor
        self.exit_arguments: tuple[object, object, object] | None = None

    def __enter__(self) -> object:
        """Yield the inert cursor for the one guarded transition call."""

        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:  # type: ignore[no-untyped-def]
        """Retain exit evidence; production uses it to commit or roll back."""

        self.exit_arguments = (exc_type, exc_value, traceback)
        return None


class RecordingDatabase:
    """Supply one inspectable short transaction scope to the composition."""

    def __init__(self, cursor: object) -> None:
        """Build the one context used by this test database object."""

        self.context = RecordingWriteCursor(cursor)

    def write_cursor(self) -> RecordingWriteCursor:
        """Match the structural capability of the restricted Psycopg adapter."""

        return self.context


def lease() -> DemucsTaskLease:
    """Return one current running lease for the pure-adapter call shape."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 20, 12, 15, tzinfo=UTC),
    )


def classification() -> DemucsRunningFailureClassification:
    """Return a reviewed after-model MinIO artifact-write failure category."""

    return DemucsRunningFailureClassification(
        disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
        retry_code=DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE,
    )


class DemucsRunningFailureTransitionCommitTests(unittest.TestCase):
    """Prove retry evidence cannot escape before commit-or-rollback exit."""

    @patch("app.running_failure_transition_commit.transition_running_demucs_failure")
    def test_committed_transition_returns_after_normal_context_exit(self, transition) -> None:
        """The wrapper forwards only cursor, lease, classification, and delay."""

        database = RecordingDatabase(cursor=object())
        expected = DemucsRunningFailureTransition(
            disposition=DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED,
            retry_schedule=DemucsRunningRetrySchedule(
                task_id=TASK_ID,
                job_id=JOB_ID,
                attempt_count=1,
                failure_code=DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE,
                available_at=datetime(2026, 9, 20, 12, 0, 30, tzinfo=UTC),
            ),
        )
        transition.return_value = expected

        returned = commit_running_demucs_failure_transition(
            database=database,  # type: ignore[arg-type]
            lease=lease(),
            classification=classification(),
        )

        self.assertIs(returned, expected)
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        transition.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            classification=classification(),
            retry_after_seconds=30,
        )

    @patch("app.running_failure_transition_commit.transition_running_demucs_failure")
    def test_no_row_commits_without_manufacturing_running_failure_evidence(self, transition) -> None:
        """Expiry/recovery races stop normally after the short transaction exits."""

        database = RecordingDatabase(cursor=object())
        transition.return_value = None

        self.assertIsNone(
            commit_running_demucs_failure_transition(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                classification=classification(),
                retry_after_seconds=45,
            )
        )
        self.assertEqual(database.context.exit_arguments, (None, None, None))
        transition.assert_called_once_with(
            database.context.cursor,
            lease=lease(),
            classification=classification(),
            retry_after_seconds=45,
        )

    @patch("app.running_failure_transition_commit.transition_running_demucs_failure")
    def test_adapter_error_escapes_through_exceptional_context_exit(self, transition) -> None:
        """A failed SQL/protocol operation cannot be presented as committed work."""

        database = RecordingDatabase(cursor=object())
        failure = RuntimeError("private fake database failure")
        transition.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            commit_running_demucs_failure_transition(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                classification=classification(),
            )

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)
        self.assertIs(database.context.exit_arguments[1], failure)

    @patch("app.running_failure_transition_commit.transition_running_demucs_failure")
    def test_missing_database_capability_prevents_the_pure_transition(self, transition) -> None:
        """No result can be durable when the caller cannot open a transaction."""

        with self.assertRaisesRegex(TypeError, "database must provide write_cursor"):
            commit_running_demucs_failure_transition(
                database=object(),  # type: ignore[arg-type]
                lease=lease(),
                classification=classification(),
            )

        transition.assert_not_called()


if __name__ == "__main__":
    unittest.main()
