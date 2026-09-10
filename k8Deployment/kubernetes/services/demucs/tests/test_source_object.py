"""Unit tests for private Demucs MinIO HeadObject verification.

Tests use a fake S3 client. They never open MinIO, PostgreSQL, RabbitMQ,
Kubernetes, or an ML runtime, and they never download a source object.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, call

from app.source_object import (
    MAX_DEMUCS_SOURCE_SIZE_BYTES,
    DemucsPermanentSourceVerificationError,
    DemucsSourceStorageProtocolError,
    DemucsSourceStorageUnavailable,
    DemucsSourceVerificationFailureCode,
    verify_claimed_demucs_source_head_object,
)
from app.task_lease import DemucsTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def lease(**overrides: object) -> DemucsTaskLease:
    """Return one already-claimed task using a stable private source key."""

    arguments: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"uploads/{JOB_ID}/mix.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 8, 12, 15, tzinfo=UTC),
    }
    arguments.update(overrides)
    return DemucsTaskLease(**arguments)  # type: ignore[arg-type]


def matching_head_response() -> dict[str, object]:
    """Return the S3 headers for one valid private canonical source upload."""

    return {
        "ContentLength": 1_024,
        "ContentType": "audio/wav",
        # Boto3/MinIO expose user metadata without the x-amz-meta- prefix.
        "Metadata": {"job-id": JOB_ID, "stem-mode": "4-stems"},
    }


class FakeS3Error(Exception):
    """Small S3-shaped error that tests error-code classification without Boto3."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("private fake S3 diagnostic")


class DemucsSourceHeadObjectTests(unittest.TestCase):
    """Prove only a claimed task's matching private object reaches later FFprobe."""

    def test_matching_head_object_returns_headers_without_downloading_audio(self) -> None:
        """The verifier makes only one HeadObject call for the leased coordinates."""

        client = MagicMock()
        client.head_object.return_value = matching_head_response()

        verified = verify_claimed_demucs_source_head_object(client, lease=lease())

        self.assertEqual(verified.size_bytes, 1_024)
        self.assertEqual(verified.content_type, "audio/wav")
        self.assertEqual(verified.object_key, f"uploads/{JOB_ID}/mix.wav")
        self.assertEqual(client.method_calls, [call.head_object(Bucket="clouddsp-uploads", Key=f"uploads/{JOB_ID}/mix.wav")])

    def test_all_canonical_audio_types_are_accepted(self) -> None:
        """The storage verifier stays aligned with the Job API's canonical MIME set."""

        for content_type in (
            "audio/wav", "audio/mpeg", "audio/flac", "audio/mp4", "audio/aac",
            "audio/ogg", "audio/aiff", "audio/webm",
        ):
            with self.subTest(content_type=content_type):
                client = MagicMock()
                client.head_object.return_value = {**matching_head_response(), "ContentType": content_type}
                self.assertEqual(
                    verify_claimed_demucs_source_head_object(client, lease=lease()).content_type,
                    content_type,
                )

    def test_known_storage_mismatches_have_bounded_permanent_categories(self) -> None:
        """No raw header/object error becomes a durable Demucs failure message."""

        cases = (
            (
                {**matching_head_response(), "ContentLength": MAX_DEMUCS_SOURCE_SIZE_BYTES + 1},
                DemucsSourceVerificationFailureCode.SIZE_LIMIT_EXCEEDED,
            ),
            (
                {**matching_head_response(), "ContentType": "application/octet-stream"},
                DemucsSourceVerificationFailureCode.UNSUPPORTED_CONTENT_TYPE,
            ),
            (
                {**matching_head_response(), "Metadata": {"job-id": "wrong", "stem-mode": "4-stems"}},
                DemucsSourceVerificationFailureCode.METADATA_MISMATCH,
            ),
            (
                {**matching_head_response(), "Metadata": {"job-id": JOB_ID, "stem-mode": "2-stems"}},
                DemucsSourceVerificationFailureCode.METADATA_MISMATCH,
            ),
        )
        for response, expected_code in cases:
            with self.subTest(code=expected_code):
                client = MagicMock()
                client.head_object.return_value = response
                with self.assertRaises(DemucsPermanentSourceVerificationError) as raised:
                    verify_claimed_demucs_source_head_object(client, lease=lease())
                self.assertEqual(raised.exception.failure_code, expected_code)

    def test_missing_object_is_permanent_but_other_client_errors_are_retryable(self) -> None:
        """A worker can retry outages without misclassifying them as bad media."""

        missing = MagicMock()
        missing.head_object.side_effect = FakeS3Error("NoSuchKey")
        with self.assertRaises(DemucsPermanentSourceVerificationError) as raised:
            verify_claimed_demucs_source_head_object(missing, lease=lease())
        self.assertEqual(raised.exception.failure_code, DemucsSourceVerificationFailureCode.OBJECT_MISSING)

        unavailable = MagicMock()
        unavailable.head_object.side_effect = FakeS3Error("AccessDenied")
        with self.assertRaises(DemucsSourceStorageUnavailable) as raised:
            verify_claimed_demucs_source_head_object(unavailable, lease=lease())
        self.assertEqual(str(raised.exception), "Demucs source HeadObject is unavailable.")

    def test_malformed_headers_metadata_or_task_identity_are_not_trusted(self) -> None:
        """Protocol errors prevent a permissive client from reaching FFprobe."""

        malformed_responses = (
            {**matching_head_response(), "ContentLength": 0},
            {**matching_head_response(), "ContentLength": True},
            {**matching_head_response(), "ContentType": ""},
            {**matching_head_response(), "Metadata": {"job-id": JOB_ID}},
            {**matching_head_response(), "Metadata": {"Job-Id": JOB_ID, "job-id": JOB_ID, "stem-mode": "4-stems"}},
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                client = MagicMock()
                client.head_object.return_value = response
                with self.assertRaises(DemucsSourceStorageProtocolError):
                    verify_claimed_demucs_source_head_object(client, lease=lease())

        with self.assertRaises(DemucsSourceStorageProtocolError):
            verify_claimed_demucs_source_head_object(MagicMock(), lease=lease(input_bucket="other-bucket"))
        with self.assertRaises(DemucsSourceStorageProtocolError):
            verify_claimed_demucs_source_head_object(
                MagicMock(),
                lease=lease(input_object_key=f"uploads/{JOB_ID}/nested/mix.wav"),
            )


if __name__ == "__main__":
    unittest.main()
