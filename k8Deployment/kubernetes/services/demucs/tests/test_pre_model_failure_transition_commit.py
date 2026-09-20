"""Tests for the short Demucs source-failure transaction composition.

The pure SQL adapter is replaced at its public seam. These tests prove only
that results leave a normal transaction scope and errors leave an exceptional
one; they create no PostgreSQL connection, MinIO/RabbitMQ request, model,
container, or Kubernetes object.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch

from app.pre_model_failure_transition import (
    DemucsPreModelFailureTransition,
    DemucsPreModelFailureTransitionDisposition,
    DemucsPreModelRetrySchedule,
)
from app.pre_model_failure_transition_commit import (
    commit_leased_demucs_pre_model_failure_transition,
)
from app.source_failure_classification import (
    DemucsPreModelFailureClassification,
    DemucsPreModelFailureDisposition,
    DemucsPreModelRetryCode,
)
from app.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"


class RecordingWriteCursor(AbstractContextManager[object]):
    """Record whether the fake transaction closes normally or exceptionally."""

    def __init__(self, cursor: object) -> None:
        """Retain the inert cursor object supplied to the patched SQL boundary."""

        self.cursor = cursor
        self.exit_arguments: tuple[object, object, object] | None = None

    def __enter__(self) -> object:
        """Return the one cursor object for the pure SQL adapter call."""

        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:  # type: ignore[no-untyped-def]
        """Save exception evidence; a real context would commit or roll back."""

        self.exit_arguments = (exc_type, exc_value, traceback)
        return None


class RecordingDatabase:
    """Provide exactly one transaction context without a driver connection."""

    def __init__(self, cursor: object) -> None:
        """Construct the recordable short transaction supplied to the wrapper."""

        self.context = RecordingWriteCursor(cursor)

    def write_cursor(self) -> RecordingWriteCursor:
        """Return the same context a restricted concrete database would expose."""

        return self.context


def lease() -> DemucsTaskLease:
    """Build a valid current pre-model lease used only as adapter input."""

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


def classification() -> DemucsPreModelFailureClassification:
    """Build the one reviewed transient source-storage classification."""

    return DemucsPreModelFailureClassification(
        disposition=DemucsPreModelFailureDisposition.RETRY_SCHEDULED,
        retry_code=DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
    )


class DemucsPreModelFailureTransitionCommitTests(unittest.TestCase):
    """Prove a transition is visible only after commit-or-rollback exit."""

    @patch(
        "app.pre_model_failure_transition_commit.transition_leased_demucs_pre_model_failure"
    )
    def test_committed_transition_returns_only_after_normal_context_exit(self, transition) -> None:
        """The wrapper forwards only the selected lease/classification/delay."""

        database = RecordingDatabase(cursor=object())
        expected = DemucsPreModelFailureTransition(
            disposition=DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED,
            retry_schedule=DemucsPreModelRetrySchedule(
                task_id=TASK_ID,
                job_id=JOB_ID,
                attempt_count=1,
                failure_code=DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
                available_at=datetime(2026, 9, 20, 12, 0, 30, tzinfo=UTC),
            ),
        )
        transition.return_value = expected

        returned = commit_leased_demucs_pre_model_failure_transition(
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

    @patch(
        "app.pre_model_failure_transition_commit.transition_leased_demucs_pre_model_failure"
    )
    def test_ownership_loss_commits_normally_without_inventing_evidence(self, transition) -> None:
        """A no-row lease race is a committed stop signal, not a new outcome."""

        database = RecordingDatabase(cursor=object())
        transition.return_value = None

        self.assertIsNone(
            commit_leased_demucs_pre_model_failure_transition(
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

    @patch(
        "app.pre_model_failure_transition_commit.transition_leased_demucs_pre_model_failure"
    )
    def test_adapter_error_escapes_through_exceptional_context_exit(self, transition) -> None:
        """A failed SQL/protocol decision cannot become a durable task result."""

        database = RecordingDatabase(cursor=object())
        failure = RuntimeError("private fake database failure")
        transition.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            commit_leased_demucs_pre_model_failure_transition(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                classification=classification(),
            )

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)
        self.assertIs(database.context.exit_arguments[1], failure)

    @patch(
        "app.pre_model_failure_transition_commit.transition_leased_demucs_pre_model_failure"
    )
    def test_missing_database_capability_stops_before_pure_sql_adapter(self, transition) -> None:
        """The caller must not classify a result as durable without a transaction."""

        with self.assertRaisesRegex(TypeError, "database must provide write_cursor"):
            commit_leased_demucs_pre_model_failure_transition(
                database=object(),  # type: ignore[arg-type]
                lease=lease(),
                classification=classification(),
            )

        transition.assert_not_called()


if __name__ == "__main__":
    unittest.main()
