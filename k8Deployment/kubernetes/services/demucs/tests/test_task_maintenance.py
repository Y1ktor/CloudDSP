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

from app.demucs_requested_message import DemucsRequestedMessage
from app.postgresql import DemucsDatabaseUnavailable
from app.recovery_request import DemucsRecoveryRequestProtocolError
from app.task_lease import (
    DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    DemucsExpiredLeaseTerminalization,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
)
from app.task_maintenance import (
    DemucsRecoveredTask,
    DemucsRecoveredTaskProtocolError,
    recover_one_demucs_task,
    renew_one_demucs_task_lease,
    terminalize_one_expired_exhausted_demucs_task,
)


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


def recovered_lease() -> DemucsTaskLease:
    """Return the current lease shape without coupling tests to SQL row parsing."""

    return DemucsTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=2,
        lease_token=LEASE_TOKEN,
        lease_expires_at=LEASE_EXPIRY,
    )


def recovered_message(**overrides: object) -> DemucsRequestedMessage:
    """Return request evidence shaped like the published v1 outbox payload."""

    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "source_bucket": "clouddsp-uploads",
        "source_object_key": f"uploads/{JOB_ID}/mix.wav",
        "stem_mode": "4-stems",
    }
    values.update(overrides)
    return DemucsRequestedMessage(**values)  # type: ignore[arg-type]


def terminalization() -> DemucsExpiredLeaseTerminalization:
    """Return one already-validated pure-SQL terminal result for composition tests."""

    return DemucsExpiredLeaseTerminalization(
        task_id=TASK_ID,
        job_id=JOB_ID,
        attempt_count=3,
        completed_at=LEASE_EXPIRY,
        job_revision=8,
        error_code=DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    )


class TaskMaintenanceCompositionTests(unittest.TestCase):
    """Prove recovery and renewal results leave one bounded transaction scope."""

    @patch("app.task_maintenance.read_current_demucs_recovery_request")
    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_idle_recovery_commits_without_inventing_a_task(self, claim, reader) -> None:
        """An empty due-task scan is normal and leaves no transaction open."""

        database = RecordingDatabase(ScriptedCursor([]))
        claim.return_value = None

        result = recover_one_demucs_task(database=database)

        self.assertIsNone(result)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        reader.assert_not_called()

    @patch("app.task_maintenance.read_current_demucs_recovery_request")
    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_recovered_pair_commits_before_it_can_start_external_work(self, claim, reader) -> None:
        """The next runtime receives a committed lease and matching request, not a cursor."""

        database = RecordingDatabase(ScriptedCursor([]))
        lease = recovered_lease()
        claim.return_value = lease
        reader.return_value = recovered_message()

        recovered = recover_one_demucs_task(
            database=database,
            uuid_factory=lambda: UUID(LEASE_TOKEN),
        )

        self.assertEqual(recovered, DemucsRecoveredTask(lease=lease, message=recovered_message()))
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        claim.assert_called_once_with(
            database.cursor,
            lease_seconds=15 * 60,
            uuid_factory=unittest.mock.ANY,
        )
        reader.assert_called_once_with(database.cursor, lease=lease)

    @patch("app.task_maintenance.read_current_demucs_recovery_request")
    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_missing_evidence_rolls_back_the_fresh_lease(self, claim, reader) -> None:
        """No current event pair may leave a newly issued lease committed."""

        database = RecordingDatabase(ScriptedCursor([]))
        claim.return_value = recovered_lease()
        reader.return_value = None

        self.assertIsNone(recover_one_demucs_task(database=database))

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])
        reader.assert_called_once_with(database.cursor, lease=claim.return_value)

    @patch("app.task_maintenance.read_current_demucs_recovery_request")
    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_invalid_evidence_error_rolls_back_and_remains_visible(self, claim, reader) -> None:
        """A corrupt event is an operator-visible fault, not a quiet idle scan."""

        database = RecordingDatabase(ScriptedCursor([]))
        claim.return_value = recovered_lease()
        reader.side_effect = DemucsRecoveryRequestProtocolError("Demucs recovery evidence is invalid.")

        with self.assertRaises(DemucsRecoveryRequestProtocolError):
            recover_one_demucs_task(database=database)

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

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

    @patch("app.task_maintenance.read_current_demucs_recovery_request")
    @patch("app.task_maintenance.claim_next_recoverable_demucs_task")
    def test_database_outage_runs_no_recovery_query(self, recover, reader) -> None:
        """The future supervisor receives a retryable error before task SQL runs."""

        with self.assertRaises(DemucsDatabaseUnavailable):
            recover_one_demucs_task(database=UnavailableDatabase())

        recover.assert_not_called()
        reader.assert_not_called()

    def test_direct_pair_construction_cannot_mix_the_lease_and_message(self) -> None:
        """A later runtime cannot manufacture recovery authority from mixed IDs."""

        lease = recovered_lease()
        with self.assertRaises(DemucsRecoveredTaskProtocolError):
            DemucsRecoveredTask(lease=lease, message=recovered_message(event_id=TASK_ID))


class ExpiredLeaseTerminalizationCompositionTests(unittest.TestCase):
    """Prove exhausted-lease finalization gets its own short database scope."""

    @patch("app.task_maintenance.finalize_next_expired_exhausted_demucs_task")
    def test_idle_terminalization_commits_without_recovery_work(self, finalize) -> None:
        """An empty final-attempt scan is normal no-mutation maintenance work."""

        database = RecordingDatabase(ScriptedCursor([]))
        finalize.return_value = None

        self.assertIsNone(terminalize_one_expired_exhausted_demucs_task(database=database))

        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        finalize.assert_called_once_with(database.cursor)

    @patch("app.task_maintenance.finalize_next_expired_exhausted_demucs_task")
    def test_terminalization_commits_only_valid_atomic_evidence(self, finalize) -> None:
        """The returned proof is visible only after the database scope exits."""

        database = RecordingDatabase(ScriptedCursor([]))
        expected = terminalization()
        finalize.return_value = expected

        self.assertIs(terminalize_one_expired_exhausted_demucs_task(database=database), expected)

        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.task_maintenance.finalize_next_expired_exhausted_demucs_task")
    def test_invalid_terminalization_rolls_back_instead_of_reporting_progress(self, finalize) -> None:
        """A future replacement cannot commit an arbitrary non-None object."""

        database = RecordingDatabase(ScriptedCursor([]))
        finalize.return_value = object()

        with self.assertRaisesRegex(TypeError, "terminalization is invalid"):
            terminalize_one_expired_exhausted_demucs_task(database=database)

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])


if __name__ == "__main__":
    unittest.main()
