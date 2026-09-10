"""Unit tests for transaction-aware composition of one source-intake message.

The fake database below models only context ordering and mocked cursor calls;
it does not connect to PostgreSQL. The fake S3 client likewise returns metadata
in memory. These tests prove message acknowledgement becomes safe only after
each actionable record has a committed-state result, without touching RabbitMQ,
MinIO, PostgreSQL, Docker, or Kubernetes.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.database_transition import (
    INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL,
    PermanentSourceFailureCategory,
)
from app.message_handler import (
    SourceIntakeCandidateOutcome,
    SourceIntakeEnvelopeOutcome,
    handle_source_intake_message,
)
from app.minio_event import DIRECT_UPLOAD_EVENT_NAME, UPLOADS_BUCKET
from app.object_storage import ObjectStorageSettings, ObjectStorageUnavailable


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
OBJECT_KEY = f"uploads/{JOB_ID}/mix.wav"


def matching_event_body(*records: object) -> bytes:
    """Serialize a normal MinIO envelope without credentials or object bytes."""

    if not records:
        records = (
            {
                "eventName": DIRECT_UPLOAD_EVENT_NAME,
                "s3": {
                    "bucket": {"name": UPLOADS_BUCKET},
                    "object": {"key": OBJECT_KEY},
                },
            },
        )
    return json.dumps({"Records": list(records)}).encode("utf-8")


def pending_row() -> dict[str, object]:
    """Return the restricted database fields for an eligible direct upload."""

    return {
        "job_id": JOB_ID,
        "source_type": "direct_upload",
        "input_bucket": UPLOADS_BUCKET,
        "input_object_key": OBJECT_KEY,
        "source_content_type": "audio/wav",
        "source_size_bytes": 1_024,
        "source_uploaded": False,
        "stem_mode": "4-stems",
        "status": "upload_pending",
        "revision": 7,
        "expires_at": datetime(2026, 9, 20, tzinfo=UTC),
    }


def matching_head_response() -> dict[str, object]:
    """Return metadata that exactly matches ``pending_row``."""

    return {
        "ContentLength": 1_024,
        "ContentType": "audio/wav",
        "Metadata": {"job-id": JOB_ID, "stem-mode": "4-stems"},
    }


def settings() -> ObjectStorageSettings:
    """Return non-secret injected settings for the fake private S3 client."""

    return ObjectStorageSettings(
        internal_endpoint="http://minio.test:9000",
        bucket_name=UPLOADS_BUCKET,
        region_name="us-east-1",
        access_key="not-a-real-access-key",
        secret_key="not-a-real-secret-key",
    )


class FakeTransactionDatabase:
    """Expose ordered read/write cursor contexts without a real database."""

    def __init__(self, *, read_cursor: MagicMock, write_cursor: MagicMock | None = None) -> None:
        self.read_cursor_mock = read_cursor
        self.write_cursor_mock = write_cursor or MagicMock()
        self.events: list[str] = []

    @contextmanager
    def read_cursor(self):  # type: ignore[no-untyped-def]
        self.events.append("read-enter")
        try:
            yield self.read_cursor_mock
        finally:
            self.events.append("read-exit")

    @contextmanager
    def write_cursor(self):  # type: ignore[no-untyped-def]
        self.events.append("write-enter")
        try:
            yield self.write_cursor_mock
        finally:
            self.events.append("write-exit")


class SourceIntakeMessageHandlerTests(unittest.TestCase):
    """Prove the composed handler respects durable ordering and no-op rules."""

    def test_valid_record_reads_then_heads_then_commits_source_and_outbox(self) -> None:
        """RabbitMQ can be acknowledged only after the full write context exits."""

        read_cursor = MagicMock()
        read_cursor.fetchone.return_value = pending_row()
        write_cursor = MagicMock()
        write_cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": True,
            "status": "source_uploaded",
            "revision": 8,
        }
        database = FakeTransactionDatabase(read_cursor=read_cursor, write_cursor=write_cursor)
        client = MagicMock()
        client.head_object.return_value = matching_head_response()

        result = handle_source_intake_message(
            matching_event_body(),
            database=database,
            object_client=client,
            object_storage_settings=settings(),
        )

        self.assertTrue(result.safe_to_acknowledge)
        self.assertEqual(result.envelope_outcome, SourceIntakeEnvelopeOutcome.HANDLED)
        self.assertEqual(
            result.candidate_results[0].outcome,
            SourceIntakeCandidateOutcome.SOURCE_UPLOADED,
        )
        self.assertEqual(database.events, ["read-enter", "read-exit", "write-enter", "write-exit"])
        client.head_object.assert_called_once_with(Bucket=UPLOADS_BUCKET, Key=OBJECT_KEY)
        # The successful state transition is followed by the durable outbox
        # insert on the same write cursor before the context can commit.
        self.assertEqual(write_cursor.execute.call_count, 2)
        self.assertEqual(
            write_cursor.execute.call_args_list[1].args[0],
            INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL,
        )

    def test_outbox_insert_failure_propagates_without_an_ack_safe_result(self) -> None:
        """A failed second statement makes the caller retry the whole transaction."""

        read_cursor = MagicMock()
        read_cursor.fetchone.return_value = pending_row()
        write_cursor = MagicMock()
        write_cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": True,
            "status": "source_uploaded",
            "revision": 8,
        }
        # The update appears to run, then the outbox INSERT fails. A real
        # Psycopg transaction receives this exception and rolls the update
        # back; the concrete adapter verifies that rollback behavior separately.
        write_cursor.execute.side_effect = [None, RuntimeError("outbox unavailable")]
        database = FakeTransactionDatabase(read_cursor=read_cursor, write_cursor=write_cursor)
        client = MagicMock()
        client.head_object.return_value = matching_head_response()

        with self.assertRaisesRegex(RuntimeError, "outbox unavailable"):
            handle_source_intake_message(
                matching_event_body(),
                database=database,
                object_client=client,
                object_storage_settings=settings(),
            )

        self.assertEqual(database.events, ["read-enter", "read-exit", "write-enter", "write-exit"])
        self.assertEqual(write_cursor.execute.call_count, 2)

    def test_known_metadata_mismatch_commits_only_bounded_failed_state(self) -> None:
        """A bad label is durable failure, not a raw S3 error or retry loop."""

        read_cursor = MagicMock()
        read_cursor.fetchone.return_value = pending_row()
        write_cursor = MagicMock()
        write_cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": False,
            "status": "failed",
            "revision": 8,
        }
        database = FakeTransactionDatabase(read_cursor=read_cursor, write_cursor=write_cursor)
        client = MagicMock()
        client.head_object.return_value = {
            **matching_head_response(),
            "Metadata": {"job-id": "wrong", "stem-mode": "4-stems"},
        }

        result = handle_source_intake_message(
            matching_event_body(),
            database=database,
            object_client=client,
            object_storage_settings=settings(),
        )

        self.assertEqual(result.candidate_results[0].outcome, SourceIntakeCandidateOutcome.FAILED)
        self.assertEqual(database.events, ["read-enter", "read-exit", "write-enter", "write-exit"])
        # The first write parameter is the fixed reviewed category. Object
        # metadata itself never reaches the durable error field.
        self.assertEqual(
            write_cursor.execute.call_args.args[1][0],
            PermanentSourceFailureCategory.METADATA_MISMATCH.value,
        )

    def test_non_actionable_row_never_calls_minio_or_opens_write_transaction(self) -> None:
        """A duplicate or later-stage notification becomes a safe acknowledgement."""

        read_cursor = MagicMock()
        read_cursor.fetchone.return_value = None
        database = FakeTransactionDatabase(read_cursor=read_cursor)
        client = MagicMock()

        result = handle_source_intake_message(
            matching_event_body(),
            database=database,
            object_client=client,
            object_storage_settings=settings(),
        )

        self.assertEqual(
            result.candidate_results[0].outcome,
            SourceIntakeCandidateOutcome.NO_LONGER_PENDING,
        )
        self.assertEqual(database.events, ["read-enter", "read-exit"])
        client.head_object.assert_not_called()

    def test_invalid_envelope_is_safe_to_acknowledge_without_dependency_calls(self) -> None:
        """Retrying malformed JSON cannot produce a valid source upload later."""

        database = FakeTransactionDatabase(read_cursor=MagicMock())
        client = MagicMock()

        result = handle_source_intake_message(
            b"not-json",
            database=database,
            object_client=client,
            object_storage_settings=settings(),
        )

        self.assertTrue(result.safe_to_acknowledge)
        self.assertEqual(result.envelope_outcome, SourceIntakeEnvelopeOutcome.INVALID_ENVELOPE)
        self.assertEqual(database.events, [])
        client.head_object.assert_not_called()

    def test_transient_minio_failure_propagates_without_a_write_or_ack_result(self) -> None:
        """The future AMQP adapter must retry rather than acknowledge an outage."""

        read_cursor = MagicMock()
        read_cursor.fetchone.return_value = pending_row()
        database = FakeTransactionDatabase(read_cursor=read_cursor)
        client = MagicMock()
        client.head_object.side_effect = RuntimeError("private transport diagnostic")

        with self.assertRaises(ObjectStorageUnavailable):
            handle_source_intake_message(
                matching_event_body(),
                database=database,
                object_client=client,
                object_storage_settings=settings(),
            )

        self.assertEqual(database.events, ["read-enter", "read-exit"])
        database.write_cursor_mock.execute.assert_not_called()
