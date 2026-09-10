"""Unit tests for the one-stem private Demucs MinIO upload adapter.

The fake clients consume ordinary file-like request bodies in memory.  No test
opens Boto3, MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.demucs_artifact_upload import (
    DemucsArtifactUploadConsistencyError,
    DemucsArtifactUploadUnavailable,
    upload_demucs_stem_object,
)
from app.demucs_output_object import DemucsStemOutputObject


JOB_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"


def output_object(root: Path) -> tuple[DemucsStemOutputObject, bytes]:
    """Create one self-consistent four-stem `drums` object plan for a test."""

    contents = b"private-demucs-drums-wav-bytes"
    # A few tests keep independent plans under named child directories, so the
    # reusable fixture owns creation of every parent rather than its callers.
    root.mkdir(parents=True, exist_ok=True)
    local_path = root / "drums.wav"
    local_path.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    return (
        DemucsStemOutputObject(
            bucket="clouddsp-uploads",
            object_key=f"stems/{JOB_ID}/drums.wav",
            local_path=local_path,
            content_type="audio/wav",
            content_length=len(contents),
            s3_metadata=(
                ("schema-version", "1"),
                ("producer", "demucs"),
                ("job-id", JOB_ID),
                ("task-id", TASK_ID),
                ("stem-name", "drums"),
                ("stem-mode", "4-stems"),
                ("size-bytes", str(len(contents))),
                ("sha256", digest),
            ),
        ),
        contents,
    )


class CapturingPutObjectClient:
    """A fake S3 client that reads every request-body byte in small chunks."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> dict[str, object]:
        """Capture request fields and simulate MinIO draining the full stream."""

        body = kwargs["Body"]
        received = bytearray()
        while chunk := body.read(5):
            received.extend(chunk)
        self.calls.append({**kwargs, "received": bytes(received)})
        return {"ETag": "not-used-as-a-sha256-proof"}


class PartialReadPutObjectClient:
    """A deliberately broken client that returns before consuming ContentLength."""

    def put_object(self, **kwargs: object) -> dict[str, object]:
        kwargs["Body"].read(1)
        return {}


class FailingPutObjectClient:
    """A fake client whose transport fails without exposing an SDK diagnostic."""

    def put_object(self, **_kwargs: object) -> object:
        raise RuntimeError("private endpoint details must not escape")


class DemucsArtifactUploadTests(unittest.TestCase):
    """Prove MinIO receives only the exact planned one-stem body and metadata."""

    def test_streams_exact_bytes_to_fixed_s3_arguments_and_returns_safe_receipt(self) -> None:
        """The upload uses no mutable metadata or ETag as an integrity substitute."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan, contents = output_object(Path(temporary_directory))
            client = CapturingPutObjectClient()

            receipt = upload_demucs_stem_object(client=client, output_object=plan)

            self.assertEqual(
                receipt,
                type(receipt)(
                    bucket="clouddsp-uploads",
                    object_key=f"stems/{JOB_ID}/drums.wav",
                    content_length=len(contents),
                    sha256=hashlib.sha256(contents).hexdigest(),
                ),
            )
            self.assertEqual(len(client.calls), 1)
            call = client.calls[0]
            self.assertEqual(call["Bucket"], plan.bucket)
            self.assertEqual(call["Key"], plan.object_key)
            self.assertEqual(call["ContentLength"], len(contents))
            self.assertEqual(call["ContentType"], "audio/wav")
            self.assertEqual(call["Metadata"], dict(plan.s3_metadata))
            self.assertEqual(call["received"], contents)

    def test_rejects_same_size_local_change_before_client_call_or_partial_consumption(self) -> None:
        """A path cannot gain upload success merely by retaining its old byte length."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan, _ = output_object(Path(temporary_directory))
            plan.local_path.write_bytes(b"x" * plan.content_length)
            client = CapturingPutObjectClient()
            with self.assertRaises(DemucsArtifactUploadConsistencyError):
                upload_demucs_stem_object(client=client, output_object=plan)
            self.assertEqual(client.calls, [])

            second_plan, _ = output_object(Path(temporary_directory) / "second")
            with self.assertRaises(DemucsArtifactUploadConsistencyError):
                upload_demucs_stem_object(
                    client=PartialReadPutObjectClient(),
                    output_object=second_plan,
                )

    def test_client_failure_becomes_a_safe_unavailable_category(self) -> None:
        """Raw SDK transport text must not become ordinary worker output."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            plan, _ = output_object(Path(temporary_directory))

            with self.assertRaises(DemucsArtifactUploadUnavailable) as captured:
                upload_demucs_stem_object(client=FailingPutObjectClient(), output_object=plan)

            self.assertNotIn("private endpoint", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
