"""Unit tests for the narrow upload-intake PostgreSQL transition boundary.

No test connects to PostgreSQL, MinIO, RabbitMQ, Docker, or Kubernetes.  A
mock cursor records exactly what a future restricted database connection would
execute, allowing the SQL predicates and idempotency outcomes to be reviewed
without mutating a real user job.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.database_transition import (
    DEMUCS_OUTBOX_PAYLOAD_VERSION,
    FIND_PENDING_DIRECT_UPLOAD_SQL,
    INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL,
    MARK_PERMANENTLY_INVALID_SOURCE_SQL,
    MARK_VERIFIED_SOURCE_UPLOADED_SQL,
    DatabaseTransitionProtocolError,
    PendingDirectUpload,
    PermanentSourceFailureCategory,
    SourceUploadTransitionOutcome,
    find_pending_direct_upload,
    mark_permanently_invalid_source,
    mark_verified_source_uploaded_and_enqueue_demucs,
)
from app.minio_event import DIRECT_UPLOAD_EVENT_NAME, UPLOADS_BUCKET, SourceUploadCandidate


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
OBJECT_KEY = f"uploads/{JOB_ID}/mix.wav"


def candidate() -> SourceUploadCandidate:
    """Return a parser-shaped direct-upload candidate with no sensitive data."""

    return SourceUploadCandidate(
        record_index=0,
        event_name=DIRECT_UPLOAD_EVENT_NAME,
        bucket_name=UPLOADS_BUCKET,
        object_key=OBJECT_KEY,
        job_id=JOB_ID,
        source_filename="mix.wav",
    )


def pending_row() -> dict[str, object]:
    """Return only the restricted-column row expected from PostgreSQL."""

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


def pending_direct_upload() -> PendingDirectUpload:
    """Return the validated lookup result supplied to the write transaction."""

    return PendingDirectUpload(
        job_id=JOB_ID,
        input_bucket=UPLOADS_BUCKET,
        input_object_key=OBJECT_KEY,
        source_content_type="audio/wav",
        source_size_bytes=1_024,
        stem_mode="4-stems",
        revision=7,
        expires_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


class FindPendingDirectUploadTests(unittest.TestCase):
    """Prove the lookup narrows by object identity and durable pending state."""

    def test_binds_candidate_identity_and_returns_only_restricted_fields(self) -> None:
        """The lookup does not interpolate an object key or read owner/artifacts."""

        cursor = MagicMock()
        cursor.fetchone.return_value = pending_row()

        pending = find_pending_direct_upload(cursor, candidate=candidate())

        self.assertEqual(pending.job_id, JOB_ID)
        self.assertEqual(pending.input_object_key, OBJECT_KEY)
        self.assertEqual(pending.revision, 7)
        cursor.execute.assert_called_once_with(
            FIND_PENDING_DIRECT_UPLOAD_SQL,
            (JOB_ID, UPLOADS_BUCKET, OBJECT_KEY),
        )
        self.assertIn("source_type = 'direct_upload'", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertIn("status = 'upload_pending'", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertIn("source_uploaded = FALSE", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertIn("expires_at > CURRENT_TIMESTAMP", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertNotIn("owner_sub", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertNotIn("stems", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertNotIn("midi", FIND_PENDING_DIRECT_UPLOAD_SQL)
        self.assertNotIn("UPDATE", FIND_PENDING_DIRECT_UPLOAD_SQL)

    def test_absent_or_already_advanced_row_is_one_non_actionable_result(self) -> None:
        """No broad follow-up query can reveal why a row was not eligible."""

        cursor = MagicMock()
        cursor.fetchone.return_value = None

        self.assertIsNone(find_pending_direct_upload(cursor, candidate=candidate()))
        cursor.execute.assert_called_once_with(
            FIND_PENDING_DIRECT_UPLOAD_SQL,
            (JOB_ID, UPLOADS_BUCKET, OBJECT_KEY),
        )

    def test_unexpected_selected_row_fails_closed(self) -> None:
        """A driver/schema mismatch cannot be mistaken for an absent job."""

        cursor = MagicMock()
        invalid_row = pending_row()
        invalid_row["input_object_key"] = f"uploads/{JOB_ID}/another.wav"
        cursor.fetchone.return_value = invalid_row

        with self.assertRaises(DatabaseTransitionProtocolError):
            find_pending_direct_upload(cursor, candidate=candidate())


class SourceUploadStateTransitionTests(unittest.TestCase):
    """Prove both mutations are compare-and-transition operations, not writes by ID."""

    def test_verified_object_transition_and_demucs_outbox_insert_share_one_cursor(self) -> None:
        """The winning transition creates its durable work promise before commit."""

        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": True,
            "status": "source_uploaded",
            "revision": 8,
        }

        change = mark_verified_source_uploaded_and_enqueue_demucs(
            cursor,
            candidate=candidate(),
            pending=pending_direct_upload(),
            expected_revision=7,
        )

        self.assertEqual(change.outcome, SourceUploadTransitionOutcome.SOURCE_UPLOADED)
        self.assertEqual(change.resulting_revision, 8)
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(
            cursor.execute.call_args_list[0].args,
            (
                MARK_VERIFIED_SOURCE_UPLOADED_SQL,
                (JOB_ID, UPLOADS_BUCKET, OBJECT_KEY, 7),
            ),
        )
        outbox_query, outbox_params = cursor.execute.call_args_list[1].args
        self.assertEqual(outbox_query, INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL)
        event_id, outbox_job_id, serialized_payload = outbox_params
        self.assertRegex(event_id, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertEqual(outbox_job_id, JOB_ID)
        self.assertEqual(
            json.loads(serialized_payload),
            {
                "schema_version": DEMUCS_OUTBOX_PAYLOAD_VERSION,
                "job_id": JOB_ID,
                "source": {"bucket": UPLOADS_BUCKET, "object_key": OBJECT_KEY},
                "stem_mode": "4-stems",
            },
        )
        self.assertIn("revision = %s", MARK_VERIFIED_SOURCE_UPLOADED_SQL)
        self.assertIn("revision = revision + 1", MARK_VERIFIED_SOURCE_UPLOADED_SQL)
        self.assertIn("error_message = NULL", MARK_VERIFIED_SOURCE_UPLOADED_SQL)
        self.assertNotIn("INSERT", MARK_VERIFIED_SOURCE_UPLOADED_SQL)
        self.assertNotIn("DELETE", MARK_VERIFIED_SOURCE_UPLOADED_SQL)
        self.assertIn("ON CONFLICT ON CONSTRAINT", INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertIn("DO NOTHING", INSERT_PENDING_DEMUCS_OUTBOX_EVENT_SQL)
        self.assertNotIn("presigned", serialized_payload)
        self.assertNotIn("password", serialized_payload)

    def test_duplicate_or_raced_transition_is_a_safe_no_op_without_extra_query(self) -> None:
        """At-least-once delivery cannot move a later job state backward."""

        cursor = MagicMock()
        cursor.fetchone.return_value = None

        change = mark_verified_source_uploaded_and_enqueue_demucs(
            cursor,
            candidate=candidate(),
            pending=pending_direct_upload(),
            expected_revision=7,
        )

        self.assertEqual(change.outcome, SourceUploadTransitionOutcome.NO_LONGER_PENDING)
        self.assertIsNone(change.resulting_revision)
        # A duplicate/raced notification does not create an event after its
        # guarded UPDATE finds a job that is no longer upload_pending.
        self.assertEqual(cursor.execute.call_count, 1)

    def test_permanent_failure_accepts_only_a_fixed_safe_category(self) -> None:
        """The durable error field cannot receive a raw S3 exception string."""

        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": False,
            "status": "failed",
            "revision": 8,
        }

        change = mark_permanently_invalid_source(
            cursor,
            candidate=candidate(),
            expected_revision=7,
            category=PermanentSourceFailureCategory.METADATA_MISMATCH,
        )

        self.assertEqual(change.outcome, SourceUploadTransitionOutcome.FAILED)
        cursor.execute.assert_called_once_with(
            MARK_PERMANENTLY_INVALID_SOURCE_SQL,
            (
                PermanentSourceFailureCategory.METADATA_MISMATCH.value,
                JOB_ID,
                UPLOADS_BUCKET,
                OBJECT_KEY,
                7,
            ),
        )
        self.assertIn("status = 'failed'", MARK_PERMANENTLY_INVALID_SOURCE_SQL)
        self.assertIn("error_message = %s", MARK_PERMANENTLY_INVALID_SOURCE_SQL)

        with self.assertRaises(TypeError):
            mark_permanently_invalid_source(
                cursor,
                candidate=candidate(),
                expected_revision=7,
                category="private S3 exception"  # type: ignore[arg-type]
            )

    def test_returned_revision_must_be_exactly_one_greater(self) -> None:
        """A surprising SQL result must not be acknowledged as a durable update."""

        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": True,
            "status": "source_uploaded",
            "revision": 9,
        }

        with self.assertRaises(DatabaseTransitionProtocolError):
            mark_verified_source_uploaded_and_enqueue_demucs(
                cursor,
                candidate=candidate(),
                pending=pending_direct_upload(),
                expected_revision=7,
            )

    def test_pending_identity_or_revision_mismatch_fails_before_outbox_insert(self) -> None:
        """A caller cannot pair one job update with another job's work payload."""

        cursor = MagicMock()
        cursor.fetchone.return_value = {
            "job_id": JOB_ID,
            "source_uploaded": True,
            "status": "source_uploaded",
            "revision": 8,
        }
        mismatched_pending = replace(pending_direct_upload(), revision=6)

        with self.assertRaises(DatabaseTransitionProtocolError):
            mark_verified_source_uploaded_and_enqueue_demucs(
                cursor,
                candidate=candidate(),
                pending=mismatched_pending,
                expected_revision=7,
            )

        self.assertEqual(cursor.execute.call_count, 0)
