"""Unit tests for strict durable evidence reconstruction after a retry claim.

The fake cursor only records parameterized SQL.  These tests make no
PostgreSQL, RabbitMQ, MinIO, Basic Pitch, Docker, or Kubernetes request.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.recovery_request import (
    READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL,
    BasicPitchRecoveryRequestProtocolError,
    read_current_basic_pitch_recovery_request,
)
from app.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
STEM_NAME = "vocals"
STEM_KEY = f"stems/{JOB_ID}/{STEM_NAME}.wav"
STEM_SHA256 = "a" * 64


def recovery_lease(**overrides: object) -> BasicPitchTaskLease:
    """Return a second-attempt lease made by the due-retry claim adapter."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": STEM_NAME,
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


def published_event_row(**overrides: object) -> dict[str, object]:
    """Return the exact outbox record the original broker request represented."""

    row: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stage": "basic-pitch",
        "stem_name": STEM_NAME,
        "event_type": "basic-pitch.requested",
        "publication_status": "published",
        "payload": {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": STEM_NAME,
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": STEM_KEY,
                "content_type": "audio/wav",
                "size_bytes": 101,
                "sha256": STEM_SHA256,
            },
        },
    }
    row.update(overrides)
    return row


class FakeCursor:
    """Supply one selected row and retain the query/parameters for review."""

    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class RecoveryRequestTests(unittest.TestCase):
    """Prove recovery uses only matching published durable event evidence."""

    def test_current_recovery_lease_reconstructs_the_normal_strict_request(self) -> None:
        """The returned message can enter the ordinary pre-model coordinator."""

        cursor = FakeCursor(published_event_row())

        message = read_current_basic_pitch_recovery_request(cursor, lease=recovery_lease())

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.event_id, EVENT_ID)
        self.assertEqual(message.job_id, JOB_ID)
        self.assertEqual(message.stem_object_key, STEM_KEY)
        self.assertEqual(message.stem_content_length, 101)
        self.assertEqual(message.stem_sha256, STEM_SHA256)
        self.assertEqual(
            cursor.calls,
            [
                (
                    READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL,
                    (
                        TASK_ID,
                        JOB_ID,
                        STEM_NAME,
                        EVENT_ID,
                        "clouddsp-uploads",
                        STEM_KEY,
                        "4-stems",
                        2,
                        RECOVERY_TOKEN,
                    ),
                )
            ],
        )
        self.assertIn("JOIN public.outbox_events", READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL)
        self.assertIn("task.lease_expires_at > CURRENT_TIMESTAMP", READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL)
        self.assertNotIn("UPDATE public.processing_tasks", READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL)
        self.assertNotIn("FOR UPDATE", READ_CURRENT_BASIC_PITCH_RECOVERY_REQUEST_SQL)

    def test_missing_current_pair_is_normal_ownership_loss(self) -> None:
        """A stale recovered lease must stop without an arbitrary fallback event."""

        cursor = FakeCursor(None)

        self.assertIsNone(read_current_basic_pitch_recovery_request(cursor, lease=recovery_lease()))
        self.assertEqual(len(cursor.calls), 1)

    def test_first_attempt_or_invalid_recovery_lease_never_queries_the_outbox(self) -> None:
        """Recovery cannot bypass the original AMQP parser/first-claim route."""

        invalid_leases = (
            recovery_lease(attempt_count=1),
            recovery_lease(input_object_key=f"stems/{JOB_ID}/bass.wav"),
            # A different *canonical* token is a normal stale-owner case
            # handled by SQL.  This malformed spelling is rejected locally
            # before any database query can be attempted.
            recovery_lease(lease_token=RECOVERY_TOKEN.upper()),
        )
        for lease in invalid_leases:
            with self.subTest(lease=lease):
                cursor = FakeCursor(published_event_row())
                with self.assertRaises(BasicPitchRecoveryRequestProtocolError):
                    read_current_basic_pitch_recovery_request(cursor, lease=lease)
                self.assertEqual(cursor.calls, [])

    def test_nonmatching_or_malformed_durable_event_is_not_recovery_input(self) -> None:
        """A published flag alone never authorizes mismatched JSONB evidence."""

        payload = published_event_row()["payload"]
        assert isinstance(payload, dict)
        stem = payload["stem"]
        assert isinstance(stem, dict)
        bad_rows = (
            published_event_row(event_id=TASK_ID),
            published_event_row(publication_status="pending"),
            published_event_row(payload={"schema_version": 1}),
            published_event_row(
                payload={
                    **payload,
                    "stem": {
                        **stem,
                        "sha256": "A" * 64,
                    },
                }
            ),
        )
        for row in bad_rows:
            with self.subTest(row=row):
                with self.assertRaises(BasicPitchRecoveryRequestProtocolError):
                    read_current_basic_pitch_recovery_request(FakeCursor(row), lease=recovery_lease())


if __name__ == "__main__":
    unittest.main()
