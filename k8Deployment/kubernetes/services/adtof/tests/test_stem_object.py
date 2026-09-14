"""Unit tests for lease-bound ADTOF MinIO ``HeadObject`` verification.

The fake client exposes metadata only. Tests never contact MinIO/PostgreSQL,
RabbitMQ, Kubernetes, or a Boto3 SDK; they never retrieve/store a drums body or
invoke the ADTOF model.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock, call

from app.adtof_requested_message import ADTOFRequestedMessage
from app.stem_object import (
    ADTOFPermanentStemVerificationError,
    ADTOFStemStorageProtocolError,
    ADTOFStemStorageUnavailable,
    ADTOFStemVerificationFailureCode,
    verify_claimed_adtof_stem_head_object,
)
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
DEMUCS_TASK_ID = "e9911c86-55f0-4f2d-bdfd-84e5a59386ea"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
STEM_SHA256 = "a" * 64


def message(**overrides: object) -> ADTOFRequestedMessage:
    """Return parser-shaped persistent-event evidence for one drums stem."""

    arguments: dict[str, object] = {
        "event_id": EVENT_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "stem_bucket": "clouddsp-uploads",
        "stem_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_content_length": 1_024,
        "stem_sha256": STEM_SHA256,
    }
    arguments.update(overrides)
    return ADTOFRequestedMessage(**arguments)  # type: ignore[arg-type]


def lease(**overrides: object) -> ADTOFTaskLease:
    """Return one committed ADTOF lease correlated exactly with ``message``."""

    arguments: dict[str, object] = {
        "task_id": TASK_ID,
        "job_id": JOB_ID,
        "stem_name": "drums",
        "request_event_id": EVENT_ID,
        "input_bucket": "clouddsp-uploads",
        "input_object_key": f"stems/{JOB_ID}/drums.wav",
        "stem_mode": "4-stems",
        "attempt_count": 1,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
    }
    arguments.update(overrides)
    return ADTOFTaskLease(**arguments)  # type: ignore[arg-type]


def matching_head_response() -> dict[str, object]:
    """Return the exact immutable metadata inventory produced by Demucs."""

    return {
        "ContentLength": 1_024,
        "ContentType": "audio/wav",
        # Boto3/MinIO strip the `x-amz-meta-` transport prefix from user keys.
        "Metadata": {
            "schema-version": "1",
            "producer": "demucs",
            "job-id": JOB_ID,
            "task-id": DEMUCS_TASK_ID,
            "stem-name": "drums",
            "stem-mode": "4-stems",
            "size-bytes": "1024",
            "sha256": STEM_SHA256,
        },
    }


class FakeS3Error(Exception):
    """Small S3-shaped failure for classification tests without Boto3."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("private fake S3 diagnostic")


class ADTOFStemHeadObjectTests(unittest.TestCase):
    """Prove only exact claimed Demucs drums evidence reaches later download work."""

    def test_matching_object_returns_evidence_with_one_metadata_only_call(self) -> None:
        """No object body/download method exists on the verifier's client protocol."""

        client = MagicMock()
        client.head_object.return_value = matching_head_response()

        verified = verify_claimed_adtof_stem_head_object(
            client,
            lease=lease(),
            message=message(),
        )

        self.assertEqual(verified.size_bytes, 1_024)
        self.assertEqual(verified.content_type, "audio/wav")
        self.assertEqual(verified.sha256, STEM_SHA256)
        self.assertEqual(verified.object_key, f"stems/{JOB_ID}/drums.wav")
        self.assertEqual(
            client.method_calls,
            [call.head_object(Bucket="clouddsp-uploads", Key=f"stems/{JOB_ID}/drums.wav")],
        )

    def test_known_header_or_metadata_disagreements_have_bounded_permanent_codes(self) -> None:
        """Bad objects never expose raw MinIO headers as a durable error string."""

        cases = (
            (
                {**matching_head_response(), "ContentLength": 1_023},
                ADTOFStemVerificationFailureCode.SIZE_MISMATCH,
            ),
            (
                {**matching_head_response(), "ContentType": "application/octet-stream"},
                ADTOFStemVerificationFailureCode.CONTENT_TYPE_MISMATCH,
            ),
            (
                {
                    **matching_head_response(),
                    "Metadata": {**matching_head_response()["Metadata"], "sha256": "b" * 64},
                },
                ADTOFStemVerificationFailureCode.METADATA_MISMATCH,
            ),
            (
                {
                    **matching_head_response(),
                    "Metadata": {**matching_head_response()["Metadata"], "stem-mode": "2-stems"},
                },
                ADTOFStemVerificationFailureCode.METADATA_MISMATCH,
            ),
        )
        for response, expected_code in cases:
            with self.subTest(code=expected_code):
                client = MagicMock()
                client.head_object.return_value = response
                with self.assertRaises(ADTOFPermanentStemVerificationError) as raised:
                    verify_claimed_adtof_stem_head_object(client, lease=lease(), message=message())
                self.assertEqual(raised.exception.failure_code, expected_code)

    def test_missing_object_is_permanent_but_other_client_failures_are_retryable(self) -> None:
        """A future worker can retry MinIO outages without hiding absent input."""

        missing = MagicMock()
        missing.head_object.side_effect = FakeS3Error("NoSuchObject")
        with self.assertRaises(ADTOFPermanentStemVerificationError) as raised:
            verify_claimed_adtof_stem_head_object(missing, lease=lease(), message=message())
        self.assertEqual(raised.exception.failure_code, ADTOFStemVerificationFailureCode.OBJECT_MISSING)

        unavailable = MagicMock()
        unavailable.head_object.side_effect = FakeS3Error("AccessDenied")
        with self.assertRaises(ADTOFStemStorageUnavailable) as raised:
            verify_claimed_adtof_stem_head_object(unavailable, lease=lease(), message=message())
        self.assertEqual(str(raised.exception), "ADTOF stem HeadObject is unavailable.")

    def test_malformed_headers_or_metadata_are_protocol_errors(self) -> None:
        """Ambiguous/malformed MinIO evidence never becomes permissive audio input."""

        malformed_responses = (
            {**matching_head_response(), "ContentLength": 0},
            {**matching_head_response(), "ContentLength": True},
            {**matching_head_response(), "ContentType": ""},
            {**matching_head_response(), "Metadata": {"job-id": JOB_ID}},
            {
                **matching_head_response(),
                "Metadata": {**matching_head_response()["Metadata"], "Job-Id": JOB_ID},
            },
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                client = MagicMock()
                client.head_object.return_value = response
                with self.assertRaises(ADTOFStemStorageProtocolError):
                    verify_claimed_adtof_stem_head_object(client, lease=lease(), message=message())

    def test_mismatched_or_forged_lease_and_message_are_rejected_before_minio(self) -> None:
        """Direct dataclasses cannot turn this verifier into arbitrary object access."""

        for invalid_lease, invalid_message in (
            (lease(input_object_key=f"stems/{JOB_ID}/other.wav"), message()),
            (lease(), message(stem_content_length=True)),
            (lease(stem_mode="not-a-reviewed-stem-mode"), message()),
        ):
            with self.subTest(lease=invalid_lease, message=invalid_message):
                client = MagicMock()
                with self.assertRaises(ADTOFStemStorageProtocolError):
                    verify_claimed_adtof_stem_head_object(
                        client,
                        lease=invalid_lease,
                        message=invalid_message,
                    )
                client.head_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
