"""Unit tests for strict Demucs evidence reconstruction after lease recovery.

The fake cursor records parameterized SQL only.  These tests do not connect to
PostgreSQL, RabbitMQ, MinIO, Demucs, Docker, or Kubernetes.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.recovery_request import (
    READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL,
    DemucsRecoveryRequestProtocolError,
    read_current_demucs_recovery_request,
)
from app.task_lease import DemucsTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
SOURCE_KEY = f"uploads/{JOB_ID}/mix.wav"


def recovery_lease(**overrides: object) -> DemucsTaskLease:
    """Return one second-attempt lease granted by the recovery claim SQL."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": SOURCE_KEY,
        "stem_mode": "4-stems",
        "attempt_count": 2,
        "lease_token": RECOVERY_TOKEN,
        "lease_expires_at": datetime(2026, 9, 21, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return DemucsTaskLease(**values)  # type: ignore[arg-type]


def verified_event_row(**overrides: object) -> dict[str, object]:
    """Return the boolean exposed by the privileged SQL verifier.

    The restricted worker never receives a raw outbox payload during recovery.
    PostgreSQL validates that private JSON document inside the reviewed
    security-definer function and returns only this single authorization fact.
    """

    row: dict[str, object] = {"recovery_event_matches": True}
    row.update(overrides)
    return row


class FakeCursor:
    """Supply one selected row and record the parameterized read operation."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class RecoveryRequestTests(unittest.TestCase):
    """Prove recovery can execute only from matching published event evidence."""

    def test_current_recovery_lease_rebuilds_the_normal_strict_request(self) -> None:
        """The output is suitable for the ordinary source-preflight path."""

        cursor = FakeCursor(verified_event_row())

        message = read_current_demucs_recovery_request(cursor, lease=recovery_lease())

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.event_id, EVENT_ID)
        self.assertEqual(message.job_id, JOB_ID)
        self.assertEqual(message.source_bucket, "clouddsp-uploads")
        self.assertEqual(message.source_object_key, SOURCE_KEY)
        self.assertEqual(message.stem_mode, "4-stems")
        self.assertEqual(
            cursor.calls,
            [
                (
                    READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL,
                    (
                        TASK_ID,
                        JOB_ID,
                        EVENT_ID,
                        "clouddsp-uploads",
                        SOURCE_KEY,
                        "4-stems",
                        2,
                        RECOVERY_TOKEN,
                    ),
                )
            ],
        )
        self.assertIn("clouddsp_demucs_recovery_event_matches", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertIn("%s::integer", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertNotIn("payload", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertNotIn("public.outbox_events", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertNotIn("UPDATE public.processing_tasks", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertNotIn("FOR UPDATE", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)

    def test_missing_current_pair_is_normal_ownership_loss(self) -> None:
        """A stale recovery token has no fallback event or external side effect."""

        cursor = FakeCursor(None)

        self.assertIsNone(read_current_demucs_recovery_request(cursor, lease=recovery_lease()))
        self.assertEqual(len(cursor.calls), 1)

    def test_first_attempt_or_invalid_lease_never_reads_the_outbox(self) -> None:
        """Recovery cannot replace the normal AMQP parser/first-claim route."""

        invalid_leases = (
            recovery_lease(attempt_count=1),
            recovery_lease(input_object_key=f"uploads/{JOB_ID}/nested/mix.wav"),
            # A dataclass annotation is not a runtime check. The recovery
            # reader must turn a malformed hand-built value into its safe
            # protocol category before any private-coordinate SQL is issued.
            recovery_lease(stem_mode=["4-stems"]),
            recovery_lease(lease_token=RECOVERY_TOKEN.upper()),
        )
        for lease in invalid_leases:
            with self.subTest(lease=lease):
                cursor = FakeCursor(verified_event_row())
                with self.assertRaises(DemucsRecoveryRequestProtocolError):
                    read_current_demucs_recovery_request(cursor, lease=lease)
                self.assertEqual(cursor.calls, [])

    def test_false_or_malformed_verifier_result_cannot_authorize_recovery(self) -> None:
        """Only PostgreSQL's exact true result authorizes a recovery attempt."""

        bad_rows = (
            verified_event_row(recovery_event_matches=False),
            verified_event_row(recovery_event_matches=None),
            {},
        )
        for row in bad_rows:
            with self.subTest(row=row):
                with self.assertRaises(DemucsRecoveryRequestProtocolError):
                    read_current_demucs_recovery_request(FakeCursor(row), lease=recovery_lease())


if __name__ == "__main__":
    unittest.main()
