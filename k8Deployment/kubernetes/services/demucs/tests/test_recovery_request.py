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


def published_event_row(**overrides: object) -> dict[str, object]:
    """Return the immutable published event that first authorized this task."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "demucs",
        "stem_name": "",
        "event_type": "demucs.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "source": {
                "bucket": "clouddsp-uploads",
                "object_key": SOURCE_KEY,
            },
            "stem_mode": "4-stems",
        },
    }
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

        cursor = FakeCursor(published_event_row())

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
        self.assertIn("JOIN public.outbox_events", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
        self.assertIn("task.lease_expires_at > CURRENT_TIMESTAMP", READ_CURRENT_DEMUCS_RECOVERY_REQUEST_SQL)
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
                cursor = FakeCursor(published_event_row())
                with self.assertRaises(DemucsRecoveryRequestProtocolError):
                    read_current_demucs_recovery_request(cursor, lease=lease)
                self.assertEqual(cursor.calls, [])

    def test_nonmatching_or_malformed_event_cannot_authorize_recovery(self) -> None:
        """Published state alone cannot bypass exact immutable-event checks."""

        payload = published_event_row()["payload"]
        assert isinstance(payload, dict)
        source = payload["source"]
        assert isinstance(source, dict)
        bad_rows = (
            published_event_row(event_id=TASK_ID),
            published_event_row(publication_status="pending"),
            published_event_row(payload={"schema_version": 1}),
            published_event_row(
                payload={
                    **payload,
                    "source": {**source, "object_key": f"uploads/{JOB_ID}/other.wav"},
                }
            ),
        )
        for row in bad_rows:
            with self.subTest(row=row):
                with self.assertRaises(DemucsRecoveryRequestProtocolError):
                    read_current_demucs_recovery_request(FakeCursor(row), lease=recovery_lease())


if __name__ == "__main__":
    unittest.main()
