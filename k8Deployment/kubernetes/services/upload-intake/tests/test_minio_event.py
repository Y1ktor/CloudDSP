"""Unit tests for the pure MinIO-to-upload-intake event boundary.

These tests do not open RabbitMQ, contact MinIO, call PostgreSQL, create a
Kubernetes object, or publish Demucs work.  They prove only that untrusted
AMQP message bytes become narrow candidates—or explicit ignored categories—
before any later infrastructure boundary is invoked.
"""

from __future__ import annotations

import json
import unittest

from app.minio_event import (
    DIRECT_UPLOAD_EVENT_NAME,
    UPLOADS_BUCKET,
    MinioEventEnvelopeError,
    parse_direct_upload_event,
)


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"


def event_record(
    *,
    event_name: str = DIRECT_UPLOAD_EVENT_NAME,
    bucket: str = UPLOADS_BUCKET,
    object_key: str = f"uploads/{JOB_ID}/mix.wav",
) -> dict[str, object]:
    """Create one non-secret, representative MinIO notification record."""

    return {
        "eventVersion": "2.0",
        "eventSource": "minio:s3",
        "eventName": event_name,
        # These values demonstrate that the parser does not trust a notification
        # size/eTag as the durable storage proof.  The later HeadObject step owns
        # that verification.
        "eventTime": "2026-09-06T12:00:00.000Z",
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": object_key, "size": 123, "eTag": "test-etag"},
        },
    }


def event_body(*records: object) -> bytes:
    """Serialize the exact JSON-envelope shape MinIO publishes to AMQP."""

    return json.dumps({"Records": list(records)}).encode("utf-8")


class ParseDirectUploadEventTests(unittest.TestCase):
    """Prove only allowed direct-upload event records become candidates."""

    def test_normalizes_one_valid_post_notification(self) -> None:
        """A valid browser POST becomes a key-bound in-memory candidate only."""

        parsed = parse_direct_upload_event(event_body(event_record()))

        self.assertEqual(len(parsed.candidates), 1)
        self.assertEqual(parsed.ignored_records, ())
        candidate = parsed.candidates[0]
        self.assertEqual(candidate.record_index, 0)
        self.assertEqual(candidate.event_name, "s3:ObjectCreated:Post")
        self.assertEqual(candidate.bucket_name, UPLOADS_BUCKET)
        self.assertEqual(candidate.object_key, f"uploads/{JOB_ID}/mix.wav")
        self.assertEqual(candidate.job_id, JOB_ID)
        self.assertEqual(candidate.source_filename, "mix.wav")

    def test_decodes_percent_escaped_filename_exactly_once(self) -> None:
        """A doubly escaped literal percent sequence never becomes a slash."""

        parsed = parse_direct_upload_event(
            event_body(event_record(object_key=f"uploads/{JOB_ID}/mix%252Ffinal.mp3"))
        )

        candidate = parsed.candidates[0]
        self.assertEqual(candidate.source_filename, "mix%2Ffinal.mp3")
        self.assertEqual(candidate.object_key, f"uploads/{JOB_ID}/mix%2Ffinal.mp3")
        self.assertNotIn("/", candidate.source_filename)

    def test_ignores_unrelated_prefix_and_future_put_without_creating_candidates(self) -> None:
        """Outputs and future yt-dlp events cannot reach a future DB lookup yet."""

        parsed = parse_direct_upload_event(
            event_body(
                event_record(object_key=f"stems/{JOB_ID}/vocals.wav"),
                event_record(event_name="s3:ObjectCreated:Put"),
            )
        )

        self.assertEqual(parsed.candidates, ())
        self.assertEqual(
            tuple(ignored.reason for ignored in parsed.ignored_records),
            ("unsupported_object_key", "unsupported_event_name"),
        )

    def test_classifies_malformed_key_and_noncanonical_job_id(self) -> None:
        """Malformed untrusted data is visible as categories, not silently accepted."""

        parsed = parse_direct_upload_event(
            event_body(
                event_record(object_key=f"uploads/{JOB_ID}/broken%2"),
                event_record(object_key=f"uploads/{JOB_ID.upper()}/mix.wav"),
            )
        )

        self.assertEqual(parsed.candidates, ())
        self.assertEqual(
            tuple(ignored.reason for ignored in parsed.ignored_records),
            ("invalid_object_key", "noncanonical_job_id"),
        )

    def test_handles_multiple_records_independently(self) -> None:
        """One malformed sibling does not hide a valid candidate or vice versa."""

        parsed = parse_direct_upload_event(
            event_body(
                event_record(object_key=f"uploads/{JOB_ID}/accepted.flac"),
                {"eventName": DIRECT_UPLOAD_EVENT_NAME, "s3": {"bucket": {"name": UPLOADS_BUCKET}}},
                "not-a-record",
            )
        )

        self.assertEqual(tuple(candidate.source_filename for candidate in parsed.candidates), ("accepted.flac",))
        self.assertEqual(
            tuple(ignored.reason for ignored in parsed.ignored_records),
            ("invalid_object_key", "invalid_record_shape"),
        )

    def test_rejects_an_invalid_envelope_before_any_record_can_be_considered(self) -> None:
        """A non-JSON or empty envelope is a message-level protocol fault."""

        for body in (b"not-json", b'{"Records": []}', b'{"records": []}'):
            with self.subTest(body=body):
                with self.assertRaises(MinioEventEnvelopeError):
                    parse_direct_upload_event(body)

    def test_rejects_a_wrong_bucket_as_a_non_actionable_record(self) -> None:
        """A similarly shaped object in another bucket cannot select a CloudDSP job."""

        parsed = parse_direct_upload_event(event_body(event_record(bucket="unrelated-bucket")))

        self.assertEqual(parsed.candidates, ())
        self.assertEqual(parsed.ignored_records[0].reason, "unexpected_bucket")
