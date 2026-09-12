"""Unit tests for the Basic Pitch first-claim transaction composition.

The cursor and database below are in-memory test doubles. They prove exactly
when a claim decision becomes visible without connecting to PostgreSQL,
RabbitMQ, MinIO, Basic Pitch, Docker, or Kubernetes. The pure SQL behavior is
covered separately by ``test_task_lease.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch
from uuid import UUID

from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.first_claim import claim_first_basic_pitch_task
from app.postgresql import BasicPitchDatabaseUnavailable
from app.task_lease import (
    BasicPitchTaskClaimDisposition,
    BasicPitchTaskClaimInconsistency,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
OTHER_EVENT_ID = "1e5d48bd-6b8f-4e7a-9987-2a19e3e2a6ba"
STEM_KEY = f"stems/{JOB_ID}/vocals.wav"


def message() -> BasicPitchRequestedMessage:
    """Return one parser-shaped non-drum message for transaction testing."""

    return BasicPitchRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        stem_bucket="clouddsp-uploads",
        stem_object_key=STEM_KEY,
        stem_content_length=101,
        stem_sha256="a" * 64,
    )


def leased_task_row(**overrides: object) -> dict[str, object]:
    """Return the complete task projection required by first-claim SQL."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stage": "basic-pitch",
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": STEM_KEY,
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def job_row() -> dict[str, object]:
    """Return the locked Job projection eligible to create a Basic Pitch task."""

    return {
        "job_id": JOB_ID,
        "stem_mode": "4-stems",
        "status": "midi_processing",
        "revision": 8,
        "is_retained": True,
    }


def outbox_row() -> dict[str, object]:
    """Return the published durable evidence which must match the AMQP request."""

    return {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "basic-pitch",
        "stem_name": "vocals",
        "event_type": "basic-pitch.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": "vocals",
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": STEM_KEY,
                "content_type": "audio/wav",
                "size_bytes": 101,
                "sha256": "a" * 64,
            },
        },
    }


class ScriptedCursor:
    """Record parameterized SQL and return only the rows supplied by a test."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record one safe SQL/query parameter pair rather than executing it."""

        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        """Return the next scripted row and fail if the contract over-reads."""

        if not self._rows:
            raise AssertionError("first-claim adapter fetched more rows than the test supplied")
        return self._rows.pop(0)


class RecordingDatabase:
    """Model one commit-or-rollback write scope and record its final action."""

    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor = cursor
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Commit normal results and roll back any exception from the pure adapter."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Model a PostgreSQL connection failure before the first SQL statement."""

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Raise the reviewed database category without yielding a cursor."""

        raise BasicPitchDatabaseUnavailable("PostgreSQL Basic Pitch task access is unavailable.")
        yield ScriptedCursor([])  # pragma: no cover - generator typing only.


class FixedUuidFactory:
    """Provide deterministic UUID objects without weakening production UUID use."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        """Return the next pre-reviewed UUID object for an inserted lease row."""

        return UUID(next(self._values))


class BasicPitchFirstClaimCompositionTests(unittest.TestCase):
    """Prove all normal first-claim outcomes return only after transaction exit."""

    def test_new_lease_commits_before_claimed_result_returns(self) -> None:
        """A future broker layer may acknowledge only after this committed lease exists."""

        database = RecordingDatabase(
            ScriptedCursor([None, job_row(), None, outbox_row(), leased_task_row()])
        )

        result = claim_first_basic_pitch_task(
            database=database,
            message=message(),
            uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
        )

        self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.CLAIMED)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 5)

    def test_existing_matching_task_commits_before_duplicate_result_returns(self) -> None:
        """A redelivery changes no row yet still has a completed safe classification."""

        database = RecordingDatabase(ScriptedCursor([leased_task_row(status="running")]))

        result = claim_first_basic_pitch_task(database=database, message=message())

        self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 1)

    def test_missing_job_commits_the_stale_no_mutation_result(self) -> None:
        """Historic broker work cannot recreate a deleted/expired job."""

        database = RecordingDatabase(ScriptedCursor([None, None]))

        result = claim_first_basic_pitch_task(database=database, message=message())

        self.assertEqual(result.disposition, BasicPitchTaskClaimDisposition.STALE)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 2)

    def test_durable_inconsistency_rolls_back_before_the_future_broker_layer_can_see_it(self) -> None:
        """An unsafe message is not silently committed or acknowledgement-safe."""

        database = RecordingDatabase(
            ScriptedCursor([leased_task_row(request_event_id=OTHER_EVENT_ID)])
        )

        with self.assertRaises(BasicPitchTaskClaimInconsistency):
            claim_first_basic_pitch_task(database=database, message=message())

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.first_claim.claim_basic_pitch_task_for_delivery")
    def test_connection_failure_does_not_attempt_the_pure_claim_sql(self, pure_claim) -> None:
        """A retryable database outage has no durable result and remains unacknowledged."""

        with self.assertRaises(BasicPitchDatabaseUnavailable):
            claim_first_basic_pitch_task(database=UnavailableDatabase(), message=message())

        pure_claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
