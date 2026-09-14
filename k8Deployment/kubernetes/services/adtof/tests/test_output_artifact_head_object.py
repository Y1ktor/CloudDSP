"""Unit tests for the two-object ADTOF stored MinIO metadata verifier.

The tests use a mock S3-shaped client plus temporary fake-runner output plans.
They do not contact MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, call

from app.minio_upload import UploadedADTOFObject, UploadedADTOFObjects
from app.output_artifact_head_object import (
    ADTOFOutputHeadObjectFailureCode,
    ADTOFOutputHeadObjectProtocolError,
    ADTOFOutputHeadObjectUnavailable,
    ADTOFPermanentOutputHeadObjectError,
    verify_uploaded_adtof_output_head_objects,
)
from test_minio_upload import upload_plans


SECOND_TASK_ID = "9434f0c3-c384-4f1d-bbd8-af9e52e2532e"


def receipts_for(plans) -> UploadedADTOFObjects:
    """Return the bounded receipts the preceding streaming uploader would yield."""

    return UploadedADTOFObjects(
        midi=UploadedADTOFObject(
            bucket=plans.midi.bucket,
            object_key=plans.midi.object_key,
            content_length=plans.midi.content_length,
            sha256=dict(plans.midi.s3_metadata)["sha256"],
        ),
        tempo_candidate=UploadedADTOFObject(
            bucket=plans.tempo_candidate.bucket,
            object_key=plans.tempo_candidate.object_key,
            content_length=plans.tempo_candidate.content_length,
            sha256=dict(plans.tempo_candidate.s3_metadata)["sha256"],
        ),
    )


def matching_head_response(plan) -> dict[str, object]:
    """Return a Boto3/MinIO-shaped current response for one exact output plan."""

    return {
        "ContentLength": plan.content_length,
        "ContentType": plan.content_type,
        "Metadata": dict(plan.s3_metadata),
    }


class S3ShapedError(Exception):
    """Vendor-neutral exception form carrying the error map exposed by Boto3."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("raw private storage diagnostic")


class ADTOFOutputArtifactHeadObjectTests(unittest.TestCase):
    """Prove current stored evidence—not local intent—guards later completion."""

    def test_matching_pair_returns_stable_evidence_after_two_fixed_metadata_reads(self) -> None:
        """The verifier reads exactly the two deterministic private object keys."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            receipts = receipts_for(plans)
            client = MagicMock()
            client.head_object.side_effect = [
                matching_head_response(plans.midi),
                matching_head_response(plans.tempo_candidate),
            ]

            verified = verify_uploaded_adtof_output_head_objects(
                client,
                upload_objects=plans,
                upload_receipts=receipts,
            )

            self.assertEqual(verified.midi.bucket, plans.midi.bucket)
            self.assertEqual(verified.midi.object_key, plans.midi.object_key)
            self.assertEqual(verified.midi.content_type, "audio/midi")
            self.assertEqual(verified.midi.content_length, plans.midi.content_length)
            self.assertEqual(verified.midi.sha256, receipts.midi.sha256)
            self.assertEqual(verified.tempo_candidate.content_type, "application/json")
            self.assertEqual(verified.tempo_candidate.object_key, plans.tempo_candidate.object_key)
            self.assertEqual(
                client.method_calls,
                [
                    call.head_object(Bucket=plans.midi.bucket, Key=plans.midi.object_key),
                    call.head_object(
                        Bucket=plans.tempo_candidate.bucket,
                        Key=plans.tempo_candidate.object_key,
                    ),
                ],
            )

    def test_missing_or_mismatched_stored_object_returns_only_a_reviewed_failure_code(self) -> None:
        """A later retry/terminal policy never has to parse raw storage details."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            receipts = receipts_for(plans)
            mismatch_cases = (
                (
                    [
                        {
                            **matching_head_response(plans.midi),
                            "ContentLength": plans.midi.content_length + 1,
                        }
                    ],
                    ADTOFOutputHeadObjectFailureCode.SIZE_MISMATCH,
                ),
                (
                    [
                        matching_head_response(plans.midi),
                        {
                            **matching_head_response(plans.tempo_candidate),
                            "ContentType": "application/octet-stream",
                        },
                    ],
                    ADTOFOutputHeadObjectFailureCode.CONTENT_TYPE_MISMATCH,
                ),
                (
                    [
                        {
                            **matching_head_response(plans.midi),
                            "Metadata": {
                                **dict(plans.midi.s3_metadata),
                                "sha256": "b" * 64,
                            },
                        }
                    ],
                    ADTOFOutputHeadObjectFailureCode.METADATA_MISMATCH,
                ),
            )
            for responses, expected_code in mismatch_cases:
                with self.subTest(expected_code=expected_code):
                    client = MagicMock()
                    client.head_object.side_effect = responses
                    with self.assertRaises(ADTOFPermanentOutputHeadObjectError) as captured:
                        verify_uploaded_adtof_output_head_objects(
                            client,
                            upload_objects=plans,
                            upload_receipts=receipts,
                        )
                    self.assertEqual(captured.exception.failure_code, expected_code)

            missing = MagicMock()
            missing.head_object.side_effect = S3ShapedError("NoSuchKey")
            with self.assertRaises(ADTOFPermanentOutputHeadObjectError) as captured:
                verify_uploaded_adtof_output_head_objects(
                    missing,
                    upload_objects=plans,
                    upload_receipts=receipts,
                )
            self.assertEqual(
                captured.exception.failure_code,
                ADTOFOutputHeadObjectFailureCode.OBJECT_MISSING,
            )

    def test_mixed_task_evidence_or_storage_outage_cannot_produce_a_result(self) -> None:
        """Two valid-looking artifacts must still share one task's provenance."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            receipts = receipts_for(plans)
            forged_metadata = tuple(
                ("task-id", SECOND_TASK_ID) if name == "task-id" else (name, value)
                for name, value in plans.tempo_candidate.s3_metadata
            )
            mixed_task_plans = replace(
                plans,
                tempo_candidate=replace(plans.tempo_candidate, s3_metadata=forged_metadata),
            )
            client = MagicMock()
            with self.assertRaises(ADTOFOutputHeadObjectProtocolError):
                verify_uploaded_adtof_output_head_objects(
                    client,
                    upload_objects=mixed_task_plans,
                    upload_receipts=receipts,
                )
            self.assertEqual(client.method_calls, [])

            unavailable = MagicMock()
            unavailable.head_object.side_effect = RuntimeError("private MinIO endpoint must not escape")
            with self.assertRaises(ADTOFOutputHeadObjectUnavailable) as captured:
                verify_uploaded_adtof_output_head_objects(
                    unavailable,
                    upload_objects=plans,
                    upload_receipts=receipts,
                )
            self.assertNotIn("private MinIO endpoint", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
