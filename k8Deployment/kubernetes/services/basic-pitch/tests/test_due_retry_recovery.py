"""Unit tests for the one-transaction Basic Pitch due-retry composition.

The fakes prove commit/rollback and call ordering only.  No test opens
PostgreSQL, contacts RabbitMQ/MinIO, runs Basic Pitch, builds an image, or
creates a Kubernetes resource; the component SQL/document contracts have
their own focused test modules.
"""

from __future__ import annotations

import unittest
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.db.due_retry_recovery import (
    BasicPitchDueRetryRecovery,
    BasicPitchDueRetryRecoveryInconsistency,
    BasicPitchDueRetryRecoveryProtocolError,
    recover_one_due_basic_pitch_retry,
)
from app.db.task_lease import DEFAULT_BASIC_PITCH_LEASE_SECONDS, BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
STEM_KEY = f"stems/{JOB_ID}/vocals.wav"


def lease(**overrides: object) -> BasicPitchTaskLease:
    """Return one fresh second-attempt lease from the due-retry SQL adapter."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": STEM_KEY,
        "stem_mode": "4-stems",
        "attempt_count": 2,
        "lease_token": RECOVERY_TOKEN,
        "lease_expires_at": datetime(2026, 9, 12, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return BasicPitchTaskLease(**values)  # type: ignore[arg-type]


def message(**overrides: object) -> BasicPitchRequestedMessage:
    """Return the normal parser-shaped request reconstructed from outbox JSONB."""

    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stem_name": "vocals",
        "stem_bucket": "clouddsp-uploads",
        "stem_object_key": STEM_KEY,
        "stem_content_length": 101,
        "stem_sha256": "a" * 64,
    }
    values.update(overrides)
    return BasicPitchRequestedMessage(**values)  # type: ignore[arg-type]


class RecordingWriteCursor(AbstractContextManager[object]):
    """Model one database context exit so tests can observe commit versus rollback."""

    def __init__(self, cursor: object) -> None:
        self.cursor = cursor
        self.exit_arguments: tuple[object, object, object] | None = None

    def __enter__(self) -> object:
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:  # type: ignore[no-untyped-def]
        self.exit_arguments = (exc_type, exc_value, traceback)
        return None


class RecordingDatabase:
    """Provide one restricted write scope with an inspectable exit result."""

    def __init__(self, cursor: object) -> None:
        self.context = RecordingWriteCursor(cursor)

    def write_cursor(self) -> RecordingWriteCursor:
        return self.context


class FixedUuidFactory:
    """Give the pure claim adapter a deterministic token in composition tests."""

    def __init__(self, value: str) -> None:
        self._value = UUID(value)

    def __call__(self) -> UUID:
        return self._value


class DueRetryRecoveryCompositionTests(unittest.TestCase):
    """Prove an atomic lease/evidence pair is the only successful recovery result."""

    @patch("app.db.due_retry_recovery.read_current_basic_pitch_recovery_request")
    @patch("app.db.due_retry_recovery.claim_next_due_basic_pitch_retry")
    def test_pair_returns_only_after_one_normal_transaction_exit(self, claim, read) -> None:
        """A later runtime sees no lease until its matching request is committed too."""

        database = RecordingDatabase(cursor=object())
        claim.return_value = lease()
        read.return_value = message()
        uuid_factory = FixedUuidFactory(RECOVERY_TOKEN)

        recovered = recover_one_due_basic_pitch_retry(database=database, uuid_factory=uuid_factory)

        self.assertEqual(database.context.exit_arguments, (None, None, None))
        self.assertIsInstance(recovered, BasicPitchDueRetryRecovery)
        assert recovered is not None
        self.assertEqual(recovered.lease, lease())
        self.assertEqual(recovered.message, message())
        claim.assert_called_once_with(
            database.context.cursor,
            lease_seconds=DEFAULT_BASIC_PITCH_LEASE_SECONDS,
            uuid_factory=uuid_factory,
        )
        read.assert_called_once_with(database.context.cursor, lease=lease())

    @patch("app.db.due_retry_recovery.read_current_basic_pitch_recovery_request")
    @patch("app.db.due_retry_recovery.claim_next_due_basic_pitch_retry")
    def test_no_due_retry_commits_normally_without_reading_any_event(self, claim, read) -> None:
        """A genuinely idle indexed claim creates no phantom recovery work."""

        database = RecordingDatabase(cursor=object())
        claim.return_value = None

        self.assertIsNone(recover_one_due_basic_pitch_retry(database=database))

        self.assertEqual(database.context.exit_arguments, (None, None, None))
        read.assert_not_called()

    @patch("app.db.due_retry_recovery.read_current_basic_pitch_recovery_request")
    @patch("app.db.due_retry_recovery.claim_next_due_basic_pitch_retry")
    def test_missing_evidence_after_claim_rolls_back_the_fresh_lease(self, claim, read) -> None:
        """A partial result must not strand an unusable new leased task."""

        database = RecordingDatabase(cursor=object())
        claim.return_value = lease()
        read.return_value = None

        with self.assertRaises(BasicPitchDueRetryRecoveryInconsistency):
            recover_one_due_basic_pitch_retry(database=database)

        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], BasicPitchDueRetryRecoveryInconsistency)

    @patch("app.db.due_retry_recovery.read_current_basic_pitch_recovery_request")
    @patch("app.db.due_retry_recovery.claim_next_due_basic_pitch_retry")
    def test_adapter_error_escapes_so_the_one_scope_can_roll_back(self, claim, read) -> None:
        """Database/evidence faults must never return a partially durable pair."""

        database = RecordingDatabase(cursor=object())
        claim.return_value = lease()
        failure = RuntimeError("private test-only database failure")
        read.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            recover_one_due_basic_pitch_retry(database=database)

        self.assertIs(raised.exception, failure)
        assert database.context.exit_arguments is not None
        self.assertIs(database.context.exit_arguments[0], RuntimeError)

    def test_direct_pair_construction_cannot_mix_stems_or_first_attempts(self) -> None:
        """A later runtime cannot build a recovered pair from unrelated evidence."""

        with self.assertRaises(BasicPitchDueRetryRecoveryProtocolError):
            BasicPitchDueRetryRecovery(lease=lease(attempt_count=1), message=message())
        with self.assertRaises(BasicPitchDueRetryRecoveryProtocolError):
            BasicPitchDueRetryRecovery(
                lease=lease(),
                message=message(stem_name="bass", stem_object_key=f"stems/{JOB_ID}/bass.wav"),
            )


if __name__ == "__main__":
    unittest.main()
