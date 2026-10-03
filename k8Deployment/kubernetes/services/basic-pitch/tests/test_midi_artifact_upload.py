"""Unit tests for the one-object restricted Basic Pitch MinIO upload adapter.

The fake clients drain a local request body only; these tests do not import
Boto3 or connect to MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.artifacts.midi_artifact_upload import (
    BasicPitchMidiUploadConsistencyError,
    BasicPitchMidiUploadContractError,
    BasicPitchMidiUploadUnavailable,
    upload_basic_pitch_midi_object,
)
from app.artifacts.midi_output_object import BasicPitchMidiOutputObject, build_basic_pitch_midi_output_object
from test_midi_output_object import artifact, lease, message


class CapturingPutObjectClient:
    """A fake S3 client that consumes all body bytes in deliberately small reads."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        """Capture one request while simulating MinIO consuming its full stream."""

        body = kwargs["Body"]
        received = bytearray()
        while chunk := body.read(5):
            received.extend(chunk)
        self.calls.append({**kwargs, "received": bytes(received)})
        return {"ETag": "not-a-portable-sha256-proof"}


class PartialReadPutObjectClient:
    """A broken client that returns before reading the promised ContentLength."""

    def put_object(self, **kwargs: object) -> object:
        kwargs["Body"].read(1)
        return {}


class ReadAllPutObjectClient:
    """A Boto3-like caller that uses the standard no-size ``read()`` form."""

    def __init__(self) -> None:
        self.received: bytes | None = None

    def put_object(self, **kwargs: object) -> object:
        """Read the complete remaining stream in one standard file-like call."""

        self.received = kwargs["Body"].read()
        return {}


class FailingPutObjectClient:
    """A fake transport failure whose implementation text must remain private."""

    def put_object(self, **_kwargs: object) -> object:
        raise RuntimeError("private MinIO endpoint must not escape")


def output_object(root: Path) -> BasicPitchMidiOutputObject:
    """Build one fully current deterministic Basic Pitch object plan for a test."""

    return build_basic_pitch_midi_output_object(
        lease=lease(),
        message=message(),
        artifact=artifact(root),
    )


class BasicPitchMidiArtifactUploadTests(unittest.TestCase):
    """Prove MinIO receives only the exact planned private MIDI bytes/metadata."""

    def test_streams_exact_bytes_and_returns_non_sensitive_receipt(self) -> None:
        """The fixed plan, not an ETag, supplies the upload's stable identity."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            contents = plan.local_path.read_bytes()
            client = CapturingPutObjectClient()

            receipt = upload_basic_pitch_midi_object(client=client, output_object=plan)

            self.assertEqual(receipt.bucket, "clouddsp-uploads")
            self.assertEqual(receipt.object_key, "midi/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/vocals.mid")
            self.assertEqual(receipt.content_length, len(contents))
            self.assertEqual(receipt.sha256, hashlib.sha256(contents).hexdigest())
            self.assertEqual(len(client.calls), 1)
            call = client.calls[0]
            self.assertEqual(call["Bucket"], plan.bucket)
            self.assertEqual(call["Key"], plan.object_key)
            self.assertEqual(call["ContentLength"], plan.content_length)
            self.assertEqual(call["ContentType"], "audio/midi")
            self.assertEqual(call["Metadata"], dict(plan.s3_metadata))
            self.assertEqual(call["received"], contents)

    def test_supports_a_standard_no_size_body_read_without_truncating_midi(self) -> None:
        """A compatible S3 SDK may call ``read()`` rather than request chunk sizes."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            client = ReadAllPutObjectClient()

            upload_basic_pitch_midi_object(client=client, output_object=plan)

            self.assertEqual(client.received, plan.local_path.read_bytes())

    def test_stale_local_bytes_or_partial_body_consumption_cannot_report_upload_success(self) -> None:
        """Matching size alone cannot reuse evidence, and SDK consumption must be complete."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            plan.local_path.write_bytes(b"x" * plan.content_length)
            client = CapturingPutObjectClient()
            with self.assertRaises(BasicPitchMidiUploadContractError):
                upload_basic_pitch_midi_object(client=client, output_object=plan)
            self.assertEqual(client.calls, [])

            second_plan = output_object(Path(temporary_directory) / "second")
            with self.assertRaises(BasicPitchMidiUploadConsistencyError):
                upload_basic_pitch_midi_object(
                    client=PartialReadPutObjectClient(),
                    output_object=second_plan,
                )

    def test_widened_plan_or_transport_error_is_never_exposed_as_success(self) -> None:
        """The uploader rejects foreign keys and hides raw S3 transport diagnostics."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan = output_object(Path(temporary_directory))
            forged = BasicPitchMidiOutputObject(
                bucket=plan.bucket,
                object_key="midi/another-job/vocals.mid",
                local_path=plan.local_path,
                content_type=plan.content_type,
                content_length=plan.content_length,
                s3_metadata=plan.s3_metadata,
                artifact=plan.artifact,
            )
            with self.assertRaises(BasicPitchMidiUploadContractError):
                upload_basic_pitch_midi_object(client=CapturingPutObjectClient(), output_object=forged)

            with self.assertRaises(BasicPitchMidiUploadUnavailable) as captured:
                upload_basic_pitch_midi_object(client=FailingPutObjectClient(), output_object=plan)
            self.assertNotIn("private MinIO endpoint", str(captured.exception))
