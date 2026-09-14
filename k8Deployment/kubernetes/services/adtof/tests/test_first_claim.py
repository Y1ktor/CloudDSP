"""Unit tests for the ADTOF first-claim transaction composition.

The database/cursor objects below are in-memory test doubles. They prove when
a claim result becomes visible without connecting to PostgreSQL/RabbitMQ/MinIO,
running ADTOF, mounting Secrets, or creating Kubernetes resources. Pure SQL
decision details are covered separately in ``test_task_claim.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch
from uuid import UUID

from app.adtof_requested_message import ADTOFRequestedMessage
from app.first_claim import claim_first_adtof_task
from app.task_claim import ADTOFTaskClaimDisposition, ADTOFTaskClaimInconsistency


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
OTHER_EVENT_ID = "1e5d48bd-6b8f-4e7a-9987-2a19e3e2a6ba"


def message() -> ADTOFRequestedMessage:
    """Return one parser-shaped drums message for transaction tests."""

    return ADTOFRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="drums",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_content_length=101,
        stem_sha256="a" * 64,
    )


def task_row(**overrides: object) -> dict[str, object]:
    """Return the complete first-claim task projection used by the SQL adapter."""

    row: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stage": "adtof",
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def job_row() -> dict[str, object]:
    """Return the minimal reviewed projection from the Job lock function."""

    return {
        "job_id": JOB_ID,
        "stem_mode": "4-stems",
        "status": "midi_processing",
        "revision": 8,
        "is_retained": True,
    }


def outbox_row() -> dict[str, object]:
    """Return the durable event that must match the parser-shaped message."""

    return {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "adtof",
        "stem_name": "drums",
        "event_type": "adtof.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": "drums",
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{JOB_ID}/drums.wav",
                "content_type": "audio/wav",
                "size_bytes": 101,
                "sha256": "a" * 64,
            },
        },
    }


class ScriptedCursor:
    """Return one prearranged row per fetch without a database connection."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record parameterized SQL for transaction-order assertions."""

        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        """Return the next scripted row and expose unexpected over-reads."""

        if not self._rows:
            raise AssertionError("first-claim adapter fetched more rows than the test supplied")
        return self._rows.pop(0)


class RecordingDatabase:
    """Model one transaction that records normal commit or exceptional rollback."""

    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor = cursor
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Yield the cursor and expose exactly when the transaction completes."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Model a connection failure before a cursor or SQL statement exists."""

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Raise a generic retryable test error without yielding a cursor."""

        raise RuntimeError("database temporarily unavailable")
        yield ScriptedCursor([])  # pragma: no cover - generator typing only.


class FixedUuidFactory:
    """Provide deterministic UUID objects while production uses ``uuid4``."""

    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> UUID:
        """Return the next test UUID used by a first lease insert."""

        return UUID(next(self._values))


class ADTOFFirstClaimCompositionTests(unittest.TestCase):
    """Prove normal results return only after commit, never during a write scope."""

    def test_new_lease_commits_before_claimed_result_returns(self) -> None:
        """A future AMQP layer sees the claim only after its lease is durable."""

        database = RecordingDatabase(
            ScriptedCursor([None, job_row(), None, outbox_row(), task_row()])
        )

        result = claim_first_adtof_task(
            database=database,
            message=message(),
            uuid_factory=FixedUuidFactory(TASK_ID, LEASE_TOKEN),
        )

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.CLAIMED)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 5)

    def test_existing_task_commits_before_duplicate_result_returns(self) -> None:
        """A redelivery has a committed no-mutation result before it is observed."""

        database = RecordingDatabase(ScriptedCursor([task_row(status="running")]))

        result = claim_first_adtof_task(database=database, message=message())

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.DUPLICATE)
        self.assertEqual(result.duplicate_status, "running")
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 1)

    def test_missing_job_commits_the_stale_no_mutation_result(self) -> None:
        """Historic broker work cannot recreate a deleted Job inside this scope."""

        database = RecordingDatabase(ScriptedCursor([None, None]))

        result = claim_first_adtof_task(database=database, message=message())

        self.assertEqual(result.disposition, ADTOFTaskClaimDisposition.STALE)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 2)

    def test_durable_inconsistency_rolls_back_before_any_future_broker_action(self) -> None:
        """A conflicting delivery is not silently committed or acknowledgement-safe."""

        database = RecordingDatabase(ScriptedCursor([task_row(request_event_id=OTHER_EVENT_ID)]))

        with self.assertRaises(ADTOFTaskClaimInconsistency):
            claim_first_adtof_task(database=database, message=message())

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.first_claim.claim_adtof_task_for_delivery")
    def test_connection_failure_does_not_attempt_the_pure_claim_sql(self, pure_claim) -> None:
        """No durable decision exists when the transaction context cannot open."""

        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            claim_first_adtof_task(database=UnavailableDatabase(), message=message())

        pure_claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
