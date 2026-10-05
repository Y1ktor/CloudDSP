"""Unit tests for one stored Basic Pitch MIDI MinIO ``HeadObject`` verifier.

The tests use a MagicMock S3-shaped client and temporary local MIDI plan
fixtures. They make no real MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes
connection.
"""

from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

from app.artifacts.midi_artifact_head_object import (
    BasicPitchMidiHeadObjectFailureCode,
    BasicPitchMidiHeadObjectProtocolError,
    BasicPitchMidiHeadObjectUnavailable,
    BasicPitchPermanentMidiHeadObjectError,
    verify_uploaded_basic_pitch_midi_head_object,
)
from app.artifacts.midi_artifact_upload import UploadedBasicPitchMidiObject
from test_midi_artifact_upload import output_object


def receipt_for(plan) -> UploadedBasicPitchMidiObject:
    """Return the stable success receipt that the preceding uploader would produce."""

    return UploadedBasicPitchMidiObject(
        bucket=plan.bucket,
        object_key=plan.object_key,
        content_length=plan.content_length,
        sha256=dict(plan.s3_metadata)["sha256"],
    )


def matching_head_response(plan) -> dict[str, object]:
    """Return Boto3/MinIO-shaped current metadata for one exact private MIDI object."""

    return {
        "ContentLength": plan.content_length,
        "ContentType": plan.content_type,
        "Metadata": dict(plan.s3_metadata),
    }


class S3ShapedError(Exception):
    """Small vendor-neutral exception carrying the error mapping Boto3 exposes."""

    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__("raw private storage diagnostics")


class BasicPitchMidiHeadObjectTests(unittest.TestCase):
    """Prove stored metadata—not a raw upload return—authorizes later completion work."""

    def test_matching_stored_object_returns_only_stable_receipt_evidence(self) -> None:
        """The verifier makes exactly one metadata request for the fixed private key."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            receipt = receipt_for(plan)
            client = MagicMock()
            client.head_object.return_value = matching_head_response(plan)

            verified = verify_uploaded_basic_pitch_midi_head_object(
                client,
                output_object=plan,
                upload_receipt=receipt,
            )

            self.assertEqual(verified.bucket, plan.bucket)
            self.assertEqual(verified.object_key, plan.object_key)
            self.assertEqual(verified.content_length, plan.content_length)
            self.assertEqual(verified.sha256, receipt.sha256)
            self.assertEqual(
                client.method_calls,
                [call.head_object(Bucket=plan.bucket, Key=plan.object_key)],
            )

    def test_missing_or_stored_header_metadata_disagreements_are_bounded(self) -> None:
        """A later task policy gets one safe code rather than raw object details."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            receipt = receipt_for(plan)
            mismatch_cases = (
                ({"ContentLength": plan.content_length + 1, "ContentType": plan.content_type, "Metadata": dict(plan.s3_metadata)}, BasicPitchMidiHeadObjectFailureCode.SIZE_MISMATCH),
                ({"ContentLength": plan.content_length, "ContentType": "application/octet-stream", "Metadata": dict(plan.s3_metadata)}, BasicPitchMidiHeadObjectFailureCode.CONTENT_TYPE_MISMATCH),
                ({"ContentLength": plan.content_length, "ContentType": plan.content_type, "Metadata": {**dict(plan.s3_metadata), "sha256": "b" * 64}}, BasicPitchMidiHeadObjectFailureCode.METADATA_MISMATCH),
            )
            for response, expected_code in mismatch_cases:
                with self.subTest(expected_code=expected_code):
                    client = MagicMock()
                    client.head_object.return_value = response
                    with self.assertRaises(BasicPitchPermanentMidiHeadObjectError) as captured:
                        verify_uploaded_basic_pitch_midi_head_object(
                            client,
                            output_object=plan,
                            upload_receipt=receipt,
                        )
                    self.assertEqual(captured.exception.failure_code, expected_code)

            missing = MagicMock()
            missing.head_object.side_effect = S3ShapedError("NoSuchKey")
            with self.assertRaises(BasicPitchPermanentMidiHeadObjectError) as captured:
                verify_uploaded_basic_pitch_midi_head_object(
                    missing,
                    output_object=plan,
                    upload_receipt=receipt,
                )
            self.assertEqual(
                captured.exception.failure_code,
                BasicPitchMidiHeadObjectFailureCode.OBJECT_MISSING,
            )

    def test_forged_receipt_or_unavailable_storage_is_rejected_without_raw_diagnostics(self) -> None:
        """A direct frozen constructor cannot read another MIDI key or leak SDK error text."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            receipt = receipt_for(plan)
            client = MagicMock()
            with self.assertRaises(BasicPitchMidiHeadObjectProtocolError):
                verify_uploaded_basic_pitch_midi_head_object(
                    client,
                    output_object=plan,
                    upload_receipt=replace(receipt, object_key="midi/other-job/vocals.mid"),
                )
            self.assertEqual(client.method_calls, [])

            unavailable = MagicMock()
            unavailable.head_object.side_effect = RuntimeError("raw private endpoint text")
            with self.assertRaises(BasicPitchMidiHeadObjectUnavailable) as captured:
                verify_uploaded_basic_pitch_midi_head_object(
                    unavailable,
                    output_object=plan,
                    upload_receipt=receipt,
                )
            self.assertNotIn("raw private endpoint", str(captured.exception))
