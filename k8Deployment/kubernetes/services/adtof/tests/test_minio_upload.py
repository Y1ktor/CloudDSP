"""Unit tests for ADTOF's fixed two-object private MinIO upload adapter.

The fake clients consume local request bodies only. These tests neither import
Boto3 nor contact MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.processing.local_task_execution import execute_running_adtof_local_task
from app.artifacts.minio_upload import (
    ADTOFUploadConsistencyError,
    ADTOFUploadContractError,
    ADTOFUploadUnavailable,
    upload_adtof_object,
    upload_adtof_objects,
)
from app.artifacts.upload_object import build_adtof_upload_objects
from test_upload_object import FakeRunner, running_stem, tempo_payload


class CapturingPutObjectClient:
    """Fake MinIO client that proves what each request body actually supplied."""

    def __init__(self) -> None:
        """Retain only local, non-sensitive request observations for assertions."""

        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        """Drain a body in short reads, like an S3-compatible streaming client."""

        body = kwargs["Body"]
        received = bytearray()
        while chunk := body.read(7):
            received.extend(chunk)
        self.calls.append({**kwargs, "received": bytes(received)})
        return {"ETag": "not-portable-sha256-evidence"}


class PartialReadPutObjectClient:
    """Broken client that returns before consuming the supplied ContentLength."""

    def put_object(self, **kwargs: object) -> object:
        """Exercise the upload adapter's post-call byte-consumption proof."""

        kwargs["Body"].read(1)
        return {}


class FailingPutObjectClient:
    """Transport failure whose raw endpoint text must not escape the worker."""

    def put_object(self, **_kwargs: object) -> object:
        """Model a client exception without making a network request."""

        raise RuntimeError("private MinIO endpoint must not escape")


def upload_plans(work_directory: Path):
    """Produce a complete pair of current plans through the fake-runner boundary."""

    stem_directory = work_directory / "adtof-stem-example"
    stem_directory.mkdir()
    source_path = stem_directory / "stem.wav"
    source_path.write_bytes(b"input belongs to the earlier verified boundary")
    local_outputs = execute_running_adtof_local_task(
        running=running_stem(source_path),
        work_directory=work_directory,
        process_runner=FakeRunner(),  # type: ignore[arg-type]
    )
    return build_adtof_upload_objects(local_outputs=local_outputs)


class ADTOFMinIOUploadTests(unittest.TestCase):
    """Prove the private adapter sends only validated, complete ADTOF outputs."""

    def test_streams_both_exact_plans_and_returns_non_sensitive_receipts(self) -> None:
        """MinIO receives the deterministic pair, not caller-selected object data."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            client = CapturingPutObjectClient()

            receipts = upload_adtof_objects(client=client, upload_objects=plans)

            self.assertEqual(receipts.midi.bucket, "clouddsp-uploads")
            self.assertEqual(
                receipts.midi.object_key,
                "midi/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/drums.mid",
            )
            self.assertEqual(
                receipts.tempo_candidate.object_key,
                "midi/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/drums_bpm.json",
            )
            self.assertEqual(len(client.calls), 2)
            for call, plan in zip(
                client.calls,
                (plans.midi, plans.tempo_candidate),
                strict=True,
            ):
                contents = plan.local_path.read_bytes()
                self.assertEqual(call["Bucket"], plan.bucket)
                self.assertEqual(call["Key"], plan.object_key)
                self.assertEqual(call["ContentLength"], len(contents))
                self.assertEqual(call["ContentType"], plan.content_type)
                self.assertEqual(call["Metadata"], dict(plan.s3_metadata))
                self.assertEqual(call["received"], contents)
            self.assertEqual(receipts.midi.sha256, hashlib.sha256(plans.midi.local_path.read_bytes()).hexdigest())
            self.assertEqual(
                receipts.tempo_candidate.sha256,
                hashlib.sha256(plans.tempo_candidate.local_path.read_bytes()).hexdigest(),
            )

    def test_changed_or_widened_plan_cannot_begin_a_two_object_write(self) -> None:
        """Both plans validate before MIDI can be written for a bad tempo result."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            # This remains valid JSON of the expected shape, but it is not the
            # previously verified artifact. A hash change must stop both puts.
            changed_tempo = {**tempo_payload(), "bpm": 121.0}
            plans.tempo_candidate.local_path.write_text(json.dumps(changed_tempo), encoding="utf-8")
            client = CapturingPutObjectClient()
            with self.assertRaises(ADTOFUploadContractError):
                upload_adtof_objects(client=client, upload_objects=plans)
            self.assertEqual(client.calls, [])

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            forged = replace(
                plans,
                tempo_candidate=replace(plans.tempo_candidate, object_key="midi/other-job/drums_bpm.json"),
            )
            client = CapturingPutObjectClient()
            with self.assertRaises(ADTOFUploadContractError):
                upload_adtof_objects(client=client, upload_objects=forged)
            self.assertEqual(client.calls, [])

    def test_partial_consumption_or_transport_detail_never_reports_upload_success(self) -> None:
        """A client return alone is not proof, and raw MinIO details stay private."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plans = upload_plans(Path(temporary_directory))
            with self.assertRaises(ADTOFUploadConsistencyError):
                upload_adtof_object(client=PartialReadPutObjectClient(), upload_object=plans.midi)

            with self.assertRaises(ADTOFUploadUnavailable) as captured:
                upload_adtof_object(client=FailingPutObjectClient(), upload_object=plans.tempo_candidate)
            self.assertNotIn("private MinIO endpoint", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
