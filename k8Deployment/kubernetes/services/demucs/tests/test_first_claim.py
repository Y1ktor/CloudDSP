"""Unit tests for the Demucs first-claim transaction composition.

These tests use a small dictionary-row cursor and a recording transaction
context.  They never open PostgreSQL, receive/acknowledge RabbitMQ, contact
MinIO, run a model, or create a Kubernetes object.  The focused pure SQL
decisions remain covered in ``test_task_lease.py``; this file proves the
important commit-or-rollback boundary around them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from app.messaging.demucs_requested_message import DemucsRequestedMessage
from app.db.first_claim import claim_first_demucs_task
from app.db.postgresql import DemucsDatabaseUnavailable
from app.db.task_lease import (
    DemucsTaskClaimDisposition,
    DemucsTaskClaimInconsistency,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
OTHER_EVENT_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"


def message() -> DemucsRequestedMessage:
    """Return one already parser-validated delivery for transaction testing."""

    return DemucsRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        source_bucket="clouddsp-uploads",
        source_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
    )


class ScriptedCursor:
    """Record parameterized SQL and return the one scripted row sequence."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if not self._rows:
            raise AssertionError("first-claim adapter fetched more rows than the test supplied")
        return self._rows.pop(0)


class RecordingDatabase:
    """Expose one fake write scope and record whether it committed or rolled back."""

    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor = cursor
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Model the commit on normal exit and rollback on any claim failure."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Model a connection failure before any pure task query can run."""

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Raise the bounded database category without yielding a cursor."""

        raise DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable.")
        yield ScriptedCursor([])  # pragma: no cover - required only for generator typing.


class FirstClaimCompositionTests(unittest.TestCase):
    """Prove first-claim results emerge only after one bounded transaction scope."""

    def test_matching_existing_task_commits_before_duplicate_result_returns(self) -> None:
        """A redelivery changes no row, but its safe classification still exits cleanly."""

        database = RecordingDatabase(
            ScriptedCursor(
                [
                    {
                        "job_id": JOB_ID,
                        "request_event_id": EVENT_ID,
                        "status": "running",
                    }
                ]
            )
        )

        result = claim_first_demucs_task(database=database, message=message())

        self.assertEqual(result.disposition, DemucsTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 1)

    def test_safe_stale_delivery_commits_its_no_mutation_result(self) -> None:
        """A missing Job is historic broker work, not a reason to recreate state."""

        database = RecordingDatabase(ScriptedCursor([None, None]))

        result = claim_first_demucs_task(database=database, message=message())

        self.assertEqual(result.disposition, DemucsTaskClaimDisposition.STALE)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 2)

    def test_inconsistent_task_rolls_back_and_reaches_future_transport_layer(self) -> None:
        """An unsafe delivery must not be silently committed or acknowledged later."""

        database = RecordingDatabase(
            ScriptedCursor(
                [
                    {
                        "job_id": JOB_ID,
                        "request_event_id": OTHER_EVENT_ID,
                        "status": "leased",
                    }
                ]
            )
        )

        with self.assertRaises(DemucsTaskClaimInconsistency):
            claim_first_demucs_task(database=database, message=message())

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.db.first_claim.claim_demucs_task_for_delivery")
    def test_database_failure_runs_no_pure_claim_query(self, pure_claim) -> None:
        """A transient connection failure remains retryable before any task decision."""

        with self.assertRaises(DemucsDatabaseUnavailable):
            claim_first_demucs_task(database=UnavailableDatabase(), message=message())

        pure_claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
