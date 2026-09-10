"""Unit tests for Demucs recovery/renewal transaction composition.

The pure task SQL behavior is covered separately.  These in-memory tests prove
that each maintenance decision is contained in one commit-or-rollback context;
they do not connect to PostgreSQL, receive RabbitMQ work, contact MinIO, run
Demucs, sleep, or create a Kubernetes resource.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch
from uuid import UUID

from app.postgresql import DemucsDatabaseUnavailable
from app.task_lease import DemucsTaskLeaseProtocolError
from app.task_maintenance import recover_one_demucs_task, renew_one_demucs_task_lease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
LEASE_EXPIRY = datetime(2026, 9, 8, 12, 15, tzinfo=UTC)


class ScriptedCursor:
    """Record parameterized SQL and serve a predeclared row sequence."""

    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        if not self._rows:
            raise AssertionError("maintenance adapter fetched more rows than the test supplied")
        return self._rows.pop(0)


class RecordingDatabase:
    """Record the exact commit/rollback lifetime of one fake cursor scope."""

    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor = cursor
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Model the concrete Psycopg adapter's transaction behavior."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Fail before yielding a cursor, as a temporarily unavailable Service does."""

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Expose only the safe public outage category to the future supervisor."""

        raise DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable.")
        yield ScriptedCursor([])  # pragma: no cover - satisfies generator typing only.


def recovered_lease_row() -> dict[str, object]:
    """Return the full dictionary row required by the existing pure adapter."""

    return {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"uploads/{JOB_ID}/mix.wav",
        "stem_mode": "4-stems",
        "status": "leased",
        "attempt_count": 2,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": LEASE_EXPIRY,
    }


class TaskMaintenanceCompositionTests(unittest.TestCase):
    """Prove recovery and renewal results leave one bounded transaction scope."""

    def test_idle_recovery_commits_without_inventing_a_task(self) -> None:
        """An empty due-task scan is normal and leaves no transaction open."""

        database = RecordingDatabase(ScriptedCursor([None]))

        result = recover_one_demucs_task(database=database)

        self.assertIsNone(result)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 1)

    def test_recovered_lease_commits_before_it_can_start_external_work(self) -> None:
        """The next worker receives the committed token and expiry, not a live cursor."""

        database = RecordingDatabase(ScriptedCursor([recovered_lease_row()]))

        # The pure adapter rejects a returned lease with a token different from
        # the UUID it generated.  Supplying this deterministic factory models
        # PostgreSQL returning the exact recovery lease this attempt acquired.
        lease = recover_one_demucs_task(
            database=database,
            uuid_factory=lambda: UUID(LEASE_TOKEN),
        )

        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.task_id, TASK_ID)
        self.assertEqual(lease.lease_token, LEASE_TOKEN)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    def test_current_lease_renewal_commits_its_new_expiry(self) -> None:
        """A live worker may continue only after PostgreSQL accepts its own token."""

        database = RecordingDatabase(ScriptedCursor([{"lease_expires_at": LEASE_EXPIRY}]))

        expiry = renew_one_demucs_task_lease(
            database=database,
            task_id=TASK_ID,
            lease_token=LEASE_TOKEN,
        )

        self.assertEqual(expiry, LEASE_EXPIRY)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        self.assertEqual(len(database.cursor.calls), 1)

    def test_invalid_recovery_row_rolls_back_instead_of_granting_an_uncertain_lease(self) -> None:
        """A malformed driver return cannot become permission to process audio."""

        database = RecordingDatabase(ScriptedCursor([{"status": "leased"}]))

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            recover_one_demucs_task(database=database)

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_database_outage_runs_no_recovery_query(self, recover) -> None:
        """The future supervisor receives a retryable error before task SQL runs."""

        with self.assertRaises(DemucsDatabaseUnavailable):
            recover_one_demucs_task(database=UnavailableDatabase())

        recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
