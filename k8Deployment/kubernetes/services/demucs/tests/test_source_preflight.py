"""Unit tests for the lease-bound Demucs source-preflight composition.

Every dependency is a small fake. These tests never connect to MinIO,
PostgreSQL, RabbitMQ, Kubernetes, FFprobe, or the Demucs model.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.audio_probe import DemucsAudioProbeProtocolError
from app.source_download import DemucsSourceDownloadConsistencyError
from app.source_object import DemucsPermanentSourceVerificationError
from app.source_preflight import validate_claimed_demucs_source
from app.task_lease import DemucsTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


class FakeBody:
    """Minimal closeable GetObject stream used to prove cleanup/order behavior."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.closed = False

    def read(self, _amount: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        self.closed = True


class FakeMinioClient:
    """One narrow fake client that records the preflight's storage call order."""

    def __init__(self, *, head_response: object, get_response: object) -> None:
        self.head_response = head_response
        self.get_response = get_response
        self.calls: list[str] = []

    def head_object(self, *, Bucket: str, Key: str) -> object:
        self.calls.append(f"head:{Bucket}:{Key}")
        if isinstance(self.head_response, BaseException):
            raise self.head_response
        return self.head_response

    def get_object(self, *, Bucket: str, Key: str) -> object:
        self.calls.append(f"get:{Bucket}:{Key}")
        if isinstance(self.get_response, BaseException):
            raise self.get_response
        return self.get_response


class FakeFFprobeRunner:
    """Record the temporary source path without executing FFprobe."""

    def __init__(self, output: bytes) -> None:
        self.output = output
        self.source_paths: list[Path] = []

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        timeout_seconds: float,
        max_stdout_bytes: int,
    ) -> bytes:
        del cwd, timeout_seconds, max_stdout_bytes
        # The fixed FFprobe command ends in ``-i <temporary-source-path>``.
        if command[-2] != "-i":
            raise AssertionError("FFprobe adapter did not construct its fixed input flag.")
        self.source_paths.append(Path(command[-1]))
        return self.output


def lease() -> DemucsTaskLease:
    """Return a valid committed lease for the canonical private source object."""

    return DemucsTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def valid_head_response(*, content_length: int = 6, metadata: object | None = None) -> dict[str, object]:
    """Return valid HeadObject data matching the durable lease above."""

    return {
        "ContentLength": content_length,
        "ContentType": "audio/wav",
        "Metadata": metadata if metadata is not None else {"job-id": JOB_ID, "stem-mode": "4-stems"},
    }


class DemucsSourcePreflightTests(unittest.TestCase):
    """Prove the three source boundaries execute in safe order and clean up."""

    def test_happy_path_returns_evidence_only_after_temporary_source_is_removed(self) -> None:
        """HeadObject → GetObject → FFprobe has no persistent local-file result."""

        body = FakeBody([b"abc", b"def"])
        client = FakeMinioClient(
            head_response=valid_head_response(),
            get_response={"ContentLength": 6, "Body": body},
        )
        runner = FakeFFprobeRunner(
            b'{"format":{"duration":"12.5"},"streams":[{"codec_type":"audio"}]}'
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            validated = validate_claimed_demucs_source(
                client,
                lease=lease(),
                work_directory=work_directory,
                ffprobe_runner=runner,
            )

            self.assertEqual(validated.source_object.size_bytes, 6)
            self.assertEqual(str(validated.audio_probe.duration_seconds), "12.5")
            self.assertEqual(validated.audio_probe.audio_stream_count, 1)
            self.assertEqual(list(work_directory.iterdir()), [])

        self.assertEqual(
            client.calls,
            [
                f"head:clouddsp-uploads:uploads/{JOB_ID}/mix.wav",
                f"get:clouddsp-uploads:uploads/{JOB_ID}/mix.wav",
            ],
        )
        self.assertTrue(body.closed)
        self.assertEqual(len(runner.source_paths), 1)
        self.assertFalse(runner.source_paths[0].exists())

    def test_headobject_rejection_stops_download_and_ffprobe(self) -> None:
        """Wrong object metadata is permanent evidence failure before byte transfer."""

        client = FakeMinioClient(
            head_response=valid_head_response(metadata={"job-id": JOB_ID, "stem-mode": "2-stems"}),
            get_response=AssertionError("GetObject must not run"),
        )
        runner = FakeFFprobeRunner(b"not-used")
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(DemucsPermanentSourceVerificationError):
                validate_claimed_demucs_source(
                    client,
                    lease=lease(),
                    work_directory=Path(temporary_directory),
                    ffprobe_runner=runner,
                )

        self.assertEqual(client.calls, [f"head:clouddsp-uploads:uploads/{JOB_ID}/mix.wav"])
        self.assertEqual(runner.source_paths, [])

    def test_getobject_size_change_stops_ffprobe_and_closes_stream(self) -> None:
        """Changed bytes after HeadObject cannot cross into the local process layer."""

        body = FakeBody([b"abcdef"])
        client = FakeMinioClient(
            head_response=valid_head_response(),
            get_response={"ContentLength": 5, "Body": body},
        )
        runner = FakeFFprobeRunner(b"not-used")
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(DemucsSourceDownloadConsistencyError):
                validate_claimed_demucs_source(
                    client,
                    lease=lease(),
                    work_directory=work_directory,
                    ffprobe_runner=runner,
                )
            self.assertEqual(list(work_directory.iterdir()), [])

        self.assertTrue(body.closed)
        self.assertEqual(runner.source_paths, [])

    def test_ffprobe_failure_still_removes_downloaded_source_and_closes_body(self) -> None:
        """The context cleanup works when a later local-validation stage raises."""

        body = FakeBody([b"abcdef"])
        client = FakeMinioClient(
            head_response=valid_head_response(),
            get_response={"ContentLength": 6, "Body": body},
        )
        runner = FakeFFprobeRunner(b"not-json")
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(DemucsAudioProbeProtocolError):
                validate_claimed_demucs_source(
                    client,
                    lease=lease(),
                    work_directory=work_directory,
                    ffprobe_runner=runner,
                )
            self.assertEqual(list(work_directory.iterdir()), [])

        self.assertTrue(body.closed)
        self.assertEqual(len(runner.source_paths), 1)
        self.assertFalse(runner.source_paths[0].exists())


if __name__ == "__main__":
    unittest.main()
