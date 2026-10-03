"""Unit tests for strict ADTOF request recovery from a published outbox event.

The fake cursor records parameterized SQL only. These tests open no
PostgreSQL/RabbitMQ/MinIO connection, run no ADTOF process, build no image, and
make no Kubernetes change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping
import unittest

from app.db.recovery_request import (
    READ_CURRENT_ADTOF_RECOVERY_REQUEST_SQL,
    ADTOFRecoveryRequestProtocolError,
    read_current_adtof_recovery_request,
)
from app.db.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "9381d35a-355f-4fb1-bb39-32ceba7d917f"
RECOVERY_TOKEN = "63c9d8d2-11db-41c4-9cc5-79889f912f98"
STEM_KEY = f"stems/{JOB_ID}/drums.wav"


def recovery_lease(**overrides: object) -> ADTOFTaskLease:
    """Return one fresh second-attempt lease from the expired-task claim."""

    values: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": STEM_KEY,
        "stem_mode": "4-stems",
        "attempt_count": 2,
        "lease_token": RECOVERY_TOKEN,
        "lease_expires_at": datetime(2026, 9, 14, 12, 15, tzinfo=UTC),
    }
    values.update(overrides)
    return ADTOFTaskLease(**values)  # type: ignore[arg-type]


def published_event_row(**overrides: object) -> dict[str, object]:
    """Return the immutable event that originally authorized the drums task."""

    row: dict[str, object] = {
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
                "object_key": STEM_KEY,
                "content_type": "audio/wav",
                "size_bytes": 1234,
                "sha256": "a" * 64,
            },
        },
    }
    row.update(overrides)
    return row


class FakeCursor:
    """Return one selected row and preserve the exact query/parameters used."""

    def __init__(self, row: Mapping[str, object] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        """Record parameterized SQL without embedding any recovery evidence."""

        self.calls.append((query, params))

    def fetchone(self) -> Mapping[str, object] | None:
        """Return the one scripted database row for this focused reader test."""

        return self._row


class ADTOFRecoveryRequestTests(unittest.TestCase):
    """Prove recovery rebuilds only matching published normal-delivery evidence."""

    def test_current_recovery_lease_reconstructs_the_normal_strict_request(self) -> None:
        """The returned message can enter the ordinary post-claim coordinator."""

        cursor = FakeCursor(published_event_row())

        message = read_current_adtof_recovery_request(cursor, lease=recovery_lease())

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.event_id, EVENT_ID)
        self.assertEqual(message.job_id, JOB_ID)
        self.assertEqual(message.stem_object_key, STEM_KEY)
        self.assertEqual(message.stem_content_length, 1234)
        self.assertEqual(message.stem_sha256, "a" * 64)
        self.assertEqual(
            cursor.calls,
            [
                (
                    READ_CURRENT_ADTOF_RECOVERY_REQUEST_SQL,
                    (
                        TASK_ID,
                        JOB_ID,
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

    def test_no_current_row_is_normal_ownership_loss(self) -> None:
        """A stale recovery lease cannot reach MinIO or CPU processing."""

        self.assertIsNone(read_current_adtof_recovery_request(FakeCursor(None), lease=recovery_lease()))

    def test_first_attempt_or_mismatched_or_malformed_event_is_not_recovery_input(self) -> None:
        """Recovery cannot bypass AMQP parsing or weaken durable event evidence."""

        with self.assertRaises(ADTOFRecoveryRequestProtocolError):
            read_current_adtof_recovery_request(FakeCursor(published_event_row()), lease=recovery_lease(attempt_count=1))

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
                    "stem": {**stem, "sha256": "A" * 64},
                }
            ),
        )
        for row in bad_rows:
            with self.subTest(row=row):
                with self.assertRaises(ADTOFRecoveryRequestProtocolError):
                    read_current_adtof_recovery_request(FakeCursor(row), lease=recovery_lease())


if __name__ == "__main__":
    unittest.main()
