"""Unit tests for the ADTOF completion transaction composition.

The context manager and completion call are patched in memory. These tests do
not connect to PostgreSQL/MinIO/RabbitMQ, invoke ADTOF, or apply Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch

from app.db.task_completion import ADTOFTaskCompletion, ADTOFTaskCompletionProtocolError
from app.db.task_completion_commit import commit_verified_adtof_task
from test_task_completion import lease, stored_outputs, tempo_candidate


class RecordingDatabase:
    """Expose normal commit and exceptional rollback ordering without a driver."""

    def __init__(self) -> None:
        """Use an opaque cursor because the pure adapter is patched in this test."""

        self.cursor = object()
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[object]:
        """Model the production client's explicit short transaction context."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class ADTOFTaskCompletionCommitTests(unittest.TestCase):
    """Prove callers receive success only after the guarded transaction commits."""

    @patch("app.db.task_completion_commit.complete_running_adtof_task")
    def test_returns_completion_only_after_the_short_transaction_commits(self, complete) -> None:
        """Model, object storage, and broker work stay outside the row-lock scope."""

        database = RecordingDatabase()
        completion = ADTOFTaskCompletion(
            task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
            job_id="08ec1d44-3106-4fcb-91c8-5d0c78e7e046",
            completed_at=datetime(2026, 9, 13, 12, 30, tzinfo=UTC),
            resulting_revision=8,
        )
        complete.return_value = completion

        returned = commit_verified_adtof_task(
            database=database,  # type: ignore[arg-type]
            lease=lease(),
            stored_outputs=stored_outputs(),
            tempo_candidate=tempo_candidate(),
        )

        self.assertIs(returned, completion)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        complete.assert_called_once_with(
            database.cursor,
            lease=lease(),
            stored_outputs=stored_outputs(),
            tempo_candidate=tempo_candidate(),
        )

    @patch("app.db.task_completion_commit.complete_running_adtof_task")
    def test_no_row_ownership_loss_commits_no_mutation_then_returns_none(self, complete) -> None:
        """A stale worker does not turn a normal no-row result into a retry loop."""

        database = RecordingDatabase()
        complete.return_value = None

        self.assertIsNone(
            commit_verified_adtof_task(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                stored_outputs=stored_outputs(),
                tempo_candidate=tempo_candidate(),
            )
        )
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.db.task_completion_commit.complete_running_adtof_task")
    def test_completion_validation_error_rolls_back_before_the_caller_sees_it(self, complete) -> None:
        """A malformed function result cannot leave a partial durable write committed."""

        database = RecordingDatabase()
        complete.side_effect = ADTOFTaskCompletionProtocolError(
            "ADTOF task completion evidence is invalid."
        )

        with self.assertRaises(ADTOFTaskCompletionProtocolError):
            commit_verified_adtof_task(
                database=database,  # type: ignore[arg-type]
                lease=lease(),
                stored_outputs=stored_outputs(),
                tempo_candidate=tempo_candidate(),
            )
        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])


if __name__ == "__main__":
    unittest.main()
