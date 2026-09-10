"""Unit tests for the private MinIO HeadObject verification boundary.

The tests inject a small fake S3 client. They make no HTTP request, do not
download media, and do not need MinIO, PostgreSQL, RabbitMQ, Docker, or a
Kubernetes cluster. Their only purpose is to prove the metadata comparison and
safe permanent-versus-retryable error classification.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

from app.database_transition import PendingDirectUpload, PermanentSourceFailureCategory
from app.object_storage import (
    DEFAULT_MINIO_INTERNAL_ENDPOINT,
    MAX_SOURCE_SIZE_BYTES,
    ObjectStorageConfigurationError,
    ObjectStorageProtocolError,
    ObjectStorageSettings,
    ObjectStorageUnavailable,
    PermanentSourceVerificationError,
    verify_pending_direct_upload_head_object,
)


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
BUCKET = "clouddsp-uploads"
OBJECT_KEY = f"uploads/{JOB_ID}/mix.wav"


def settings() -> ObjectStorageSettings:
    """Return non-secret test-only client settings without a live endpoint."""

    return ObjectStorageSettings(
        internal_endpoint="http://minio.test:9000",
        bucket_name=BUCKET,
        region_name="us-east-1",
        access_key="not-a-real-access-key",
        secret_key="not-a-real-secret-key",
    )


def pending_direct_upload() -> PendingDirectUpload:
    """Return the narrow durable row produced by the database helper."""

    return PendingDirectUpload(
        job_id=JOB_ID,
        input_bucket=BUCKET,
        input_object_key=OBJECT_KEY,
        source_content_type="audio/wav",
        source_size_bytes=1_024,
        stem_mode="4-stems",
        revision=7,
        expires_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


def matching_head_response() -> dict[str, object]:
    """Return S3-compatible headers for one valid private source object."""

    return {
        "ContentLength": 1_024,
        "ContentType": "audio/wav",
        # MinIO/Boto3 exposes user metadata without the `x-amz-meta-` prefix.
        "Metadata": {"job-id": JOB_ID, "stem-mode": "4-stems"},
    }


class FakeS3Error(Exception):
    """Minimal S3-shaped failure object for SDK-independent error tests."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("private fake S3 diagnostic")


class HeadObjectVerificationTests(unittest.TestCase):
    """Prove MinIO metadata must match the authoritative pending database row."""

    def test_matching_private_head_object_returns_metadata_without_body_download(self) -> None:
        """The one S3 call is HeadObject for the exact bucket/key from PostgreSQL."""

        client = MagicMock()
        client.head_object.return_value = matching_head_response()

        verified = verify_pending_direct_upload_head_object(
            client,
            pending=pending_direct_upload(),
            settings=settings(),
        )

        self.assertEqual(verified.bucket_name, BUCKET)
        self.assertEqual(verified.object_key, OBJECT_KEY)
        self.assertEqual(verified.content_type, "audio/wav")
        self.assertEqual(verified.size_bytes, 1_024)
        client.head_object.assert_called_once_with(Bucket=BUCKET, Key=OBJECT_KEY)
        # MagicMock dynamically exposes every attribute, so ``hasattr`` cannot
        # prove a method was unused. Its recorded calls show the verifier made
        # exactly one metadata-only HeadObject call and no GetObject download.
        self.assertEqual(
            client.method_calls,
            [call.head_object(Bucket=BUCKET, Key=OBJECT_KEY)],
        )

    def test_metadata_and_content_type_mismatches_are_fixed_failure_categories(self) -> None:
        """No raw MinIO header or object key becomes durable job error text."""

        cases = (
            (
                {**matching_head_response(), "Metadata": {"job-id": "wrong", "stem-mode": "4-stems"}},
                PermanentSourceFailureCategory.METADATA_MISMATCH,
            ),
            (
                {**matching_head_response(), "ContentType": "audio/mpeg"},
                PermanentSourceFailureCategory.CONTENT_TYPE_MISMATCH,
            ),
            (
                {**matching_head_response(), "ContentLength": MAX_SOURCE_SIZE_BYTES + 1},
                PermanentSourceFailureCategory.SIZE_LIMIT_EXCEEDED,
            ),
            (
                {**matching_head_response(), "ContentLength": 1_023},
                PermanentSourceFailureCategory.METADATA_MISMATCH,
            ),
        )
        for response, expected_category in cases:
            with self.subTest(category=expected_category):
                client = MagicMock()
                client.head_object.return_value = response

                with self.assertRaises(PermanentSourceVerificationError) as raised:
                    verify_pending_direct_upload_head_object(
                        client,
                        pending=pending_direct_upload(),
                        settings=settings(),
                    )

                self.assertEqual(raised.exception.category, expected_category)

    def test_definite_object_absence_is_permanent_but_service_fault_is_retryable(self) -> None:
        """A later consumer can fail known-bad objects without failing outages."""

        missing_client = MagicMock()
        missing_client.head_object.side_effect = FakeS3Error("404")
        with self.assertRaises(PermanentSourceVerificationError) as raised:
            verify_pending_direct_upload_head_object(
                missing_client,
                pending=pending_direct_upload(),
                settings=settings(),
            )
        self.assertEqual(raised.exception.category, PermanentSourceFailureCategory.OBJECT_MISSING)

        unavailable_client = MagicMock()
        unavailable_client.head_object.side_effect = FakeS3Error("503")
        with self.assertRaises(ObjectStorageUnavailable) as unavailable:
            verify_pending_direct_upload_head_object(
                unavailable_client,
                pending=pending_direct_upload(),
                settings=settings(),
            )
        self.assertEqual(str(unavailable.exception), "MinIO HeadObject is temporarily unavailable.")

    def test_unexpected_bucket_is_rejected_before_any_minio_request(self) -> None:
        """The consumer cannot be redirected to another bucket by a bad row."""

        client = MagicMock()
        wrong_bucket = PendingDirectUpload(
            **{**pending_direct_upload().__dict__, "input_bucket": "another-bucket"}
        )

        with self.assertRaises(ObjectStorageProtocolError):
            verify_pending_direct_upload_head_object(client, pending=wrong_bucket, settings=settings())

        client.head_object.assert_not_called()


class ObjectStorageSettingsTests(unittest.TestCase):
    """Prove runtime configuration selects private Service DNS, never browser routing."""

    @patch.dict(
        os.environ,
        {
            "UPLOAD_INTAKE_S3_ACCESS_KEY": "test-access-key",
            "UPLOAD_INTAKE_S3_SECRET_KEY": "test-secret-key",
        },
        clear=True,
    )
    def test_settings_default_to_private_minio_service_dns(self) -> None:
        """No browser-facing .localhost host is a valid Pod S3 endpoint."""

        loaded = ObjectStorageSettings.from_environment()

        self.assertEqual(loaded.internal_endpoint, DEFAULT_MINIO_INTERNAL_ENDPOINT)
        self.assertEqual(loaded.bucket_name, BUCKET)
        self.assertEqual(loaded.addressing_style, "path")

    @patch.dict(
        os.environ,
        {
            "UPLOAD_INTAKE_S3_INTERNAL_ENDPOINT": "http://minio.localhost:8080",
            "UPLOAD_INTAKE_S3_ACCESS_KEY": "test-access-key",
            "UPLOAD_INTAKE_S3_SECRET_KEY": "test-secret-key",
        },
        clear=True,
    )
    def test_rejects_the_browser_facing_minio_host(self) -> None:
        """A Pod must not route private reads through Traefik and a Mac port."""

        with self.assertRaises(ObjectStorageConfigurationError):
            ObjectStorageSettings.from_environment()
