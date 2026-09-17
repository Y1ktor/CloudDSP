"""Unit tests for ADTOF's all-or-nothing expired-lease recovery composition.

These in-memory doubles verify transaction ordering only. They do not connect
to PostgreSQL, RabbitMQ, MinIO, or Kubernetes and they never run an ADTOF
model. The lower-level SQL claim and strict outbox reader have their own tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import unittest
from unittest.mock import patch
from uuid import UUID

from app.adtof_requested_message import ADTOFRequestedMessage
from app.recovery import (
    ADTOFRecoveredTask,
    recover_one_expired_adtof_task,
    terminalize_one_expired_exhausted_adtof_task,
)
from app.recovery_request import ADTOFRecoveryRequestProtocolError
from app.task_claim import ADTOFExpiredLeaseTerminalization, ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "9381d35a-355f-4fb1-bb39-32ceba7d917f"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"


def recovery_lease() -> ADTOFTaskLease:
    """Return the fresh second attempt produced by the pure recovery claim."""

    return ADTOFTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="drums",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_mode="4-stems",
        attempt_count=2,
        lease_token=RECOVERY_TOKEN,
        lease_expires_at=datetime(2026, 9, 14, 12, 15, tzinfo=UTC),
    )


def recovered_message() -> ADTOFRequestedMessage:
    """Return normal-delivery evidence the reader reconstructed from outbox."""

    return ADTOFRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="drums",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_content_length=1234,
        stem_sha256="a" * 64,
    )


def terminalization() -> ADTOFExpiredLeaseTerminalization:
    """Return compact evidence of one PostgreSQL-owned third-attempt failure."""

    return ADTOFExpiredLeaseTerminalization(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="drums",
        attempt_count=3,
        completed_at=datetime(2026, 9, 14, 12, 30, tzinfo=UTC),
        error_code="lease_expired_attempts_exhausted",
    )


class ScriptedCursor:
    """Opaque dictionary-row cursor stand-in shared by both mocked boundaries."""


class RecordingDatabase:
    """Record whether the composition exits its write scope normally or raises."""

    def __init__(self) -> None:
        self.cursor = ScriptedCursor()
        self.events: list[str] = []

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Model concrete adapter commit/rollback behavior for one transaction."""

        self.events.append("transaction-open")
        try:
            yield self.cursor
        except Exception:
            self.events.append("transaction-rollback")
            raise
        else:
            self.events.append("transaction-commit")


class UnavailableDatabase:
    """Model a database outage before a cursor or recovery query can exist."""

    @contextmanager
    def write_cursor(self) -> Iterator[ScriptedCursor]:
        """Raise a redacted stand-in operational failure without yielding a cursor."""

        raise RuntimeError("database temporarily unavailable")
        yield ScriptedCursor()  # pragma: no cover - satisfies generator typing.


class FixedUuidFactory:
    """Supply one deterministic UUID object to the pure recovery-claim mock."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __call__(self) -> UUID:
        """Return the canonical UUID production would normally generate randomly."""

        return UUID(self._value)


class ADTOFRecoveryCompositionTests(unittest.TestCase):
    """Prove no committed recovery lease exists without strict request evidence."""

    @patch("app.recovery.read_current_adtof_recovery_request")
    @patch("app.recovery.claim_next_expired_adtof_task")
    def test_idle_scan_commits_without_calling_the_reader(self, claim, reader) -> None:
        """No candidate is normal idle and made no mutation that needs rollback."""

        database = RecordingDatabase()
        claim.return_value = None

        result = recover_one_expired_adtof_task(database=database)

        self.assertIsNone(result)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        claim.assert_called_once_with(
            database.cursor,
            lease_seconds=15 * 60,
            uuid_factory=unittest.mock.ANY,
        )
        reader.assert_not_called()

    @patch("app.recovery.read_current_adtof_recovery_request")
    @patch("app.recovery.claim_next_expired_adtof_task")
    def test_complete_pair_commits_after_claim_then_reader(self, claim, reader) -> None:
        """The exact same cursor carries lock-protected claim and event proof."""

        database = RecordingDatabase()
        lease = recovery_lease()
        message = recovered_message()
        ordering: list[str] = []
        claim.side_effect = lambda *args, **kwargs: ordering.append("claim") or lease
        reader.side_effect = lambda *args, **kwargs: ordering.append("reader") or message
        uuid_factory = FixedUuidFactory(TASK_ID)

        result = recover_one_expired_adtof_task(
            database=database,
            lease_seconds=120,
            uuid_factory=uuid_factory,
        )

        self.assertEqual(result, ADTOFRecoveredTask(lease=lease, message=message))
        self.assertEqual(ordering, ["claim", "reader"])
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        claim.assert_called_once_with(
            database.cursor,
            lease_seconds=120,
            uuid_factory=uuid_factory,
        )
        reader.assert_called_once_with(database.cursor, lease=lease)

    @patch("app.recovery.read_current_adtof_recovery_request")
    @patch("app.recovery.claim_next_expired_adtof_task")
    def test_reader_ownership_loss_rolls_back_the_fresh_lease(self, claim, reader) -> None:
        """A claimed token without evidence must never become a durable orphan."""

        database = RecordingDatabase()
        lease = recovery_lease()
        claim.return_value = lease
        reader.return_value = None

        result = recover_one_expired_adtof_task(database=database)

        self.assertIsNone(result)
        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])
        reader.assert_called_once_with(database.cursor, lease=lease)

    @patch("app.recovery.read_current_adtof_recovery_request")
    @patch("app.recovery.claim_next_expired_adtof_task")
    def test_unsafe_event_evidence_rolls_back_and_propagates(self, claim, reader) -> None:
        """Protocol failure is not converted to idle after an otherwise-valid claim."""

        database = RecordingDatabase()
        claim.return_value = recovery_lease()
        reader.side_effect = ADTOFRecoveryRequestProtocolError(
            "ADTOF recovery evidence is invalid."
        )

        with self.assertRaises(ADTOFRecoveryRequestProtocolError):
            recover_one_expired_adtof_task(database=database)

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])

    @patch("app.recovery.read_current_adtof_recovery_request")
    @patch("app.recovery.claim_next_expired_adtof_task")
    def test_connection_failure_reaches_neither_pure_boundary(self, claim, reader) -> None:
        """A failed context entry cannot create a claim or attempt an outbox read."""

        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            recover_one_expired_adtof_task(database=UnavailableDatabase())

        claim.assert_not_called()
        reader.assert_not_called()


class ADTOFExpiredLeaseTerminalizationCompositionTests(unittest.TestCase):
    """Prove exhausted-lease failure is its own short committed transaction."""

    @patch("app.recovery.finalize_next_expired_exhausted_adtof_task")
    def test_idle_terminalization_scan_commits_without_claiming_recovery_work(self, finalize) -> None:
        """No exhausted candidate is normal no-mutation progress."""

        database = RecordingDatabase()
        finalize.return_value = None

        result = terminalize_one_expired_exhausted_adtof_task(database=database)

        self.assertIsNone(result)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])
        finalize.assert_called_once_with(database.cursor)

    @patch("app.recovery.finalize_next_expired_exhausted_adtof_task")
    def test_terminalization_commits_exact_evidence(self, finalize) -> None:
        """The separate aggregate can later inspect this task-level failure."""

        database = RecordingDatabase()
        evidence = terminalization()
        finalize.return_value = evidence

        self.assertIs(terminalize_one_expired_exhausted_adtof_task(database=database), evidence)
        self.assertEqual(database.events, ["transaction-open", "transaction-commit"])

    @patch("app.recovery.finalize_next_expired_exhausted_adtof_task")
    def test_invalid_terminalization_rolls_back_instead_of_committing_untrusted_state(self, finalize) -> None:
        """A future adapter cannot report progress from an unchecked object."""

        database = RecordingDatabase()
        finalize.return_value = object()

        with self.assertRaisesRegex(TypeError, "terminalization is invalid"):
            terminalize_one_expired_exhausted_adtof_task(database=database)

        self.assertEqual(database.events, ["transaction-open", "transaction-rollback"])


if __name__ == "__main__":
    unittest.main()
