"""Unit tests for ADTOF's bounded private drums download and cleanup.

Fakes model only one streaming S3 body. Tests never contact MinIO, PostgreSQL,
RabbitMQ, Kubernetes, Boto3, or ADTOF itself.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.artifacts.stem_download import (
    ADTOF_STEM_DOWNLOAD_CHUNK_BYTES,
    ADTOFStemDownloadConsistencyError,
    ADTOFStemDownloadProtocolError,
    ADTOFStemDownloadUnavailable,
    downloaded_verified_adtof_stem,
)
from app.artifacts.stem_object import VerifiedADTOFStemObject


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
STEM_BYTES = b"private-adtof-drums-stem-wav"
STEM_SHA256 = hashlib.sha256(STEM_BYTES).hexdigest()


class FakeBody:
    """A closeable streaming body that records each bounded read request."""

    def __init__(self, chunks: list[object]) -> None:
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, amount: int) -> object:
        self.read_sizes.append(amount)
        if not self._chunks:
            return b""
        next_chunk = self._chunks.pop(0)
        if isinstance(next_chunk, BaseException):
            raise next_chunk
        return next_chunk

    def close(self) -> None:
        self.closed = True


class FakeGetObjectClient:
    """Record only the one private GetObject request this adapter may make."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> object:
        self.calls.append({"Bucket": Bucket, "Key": Key})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def verified_stem(
    *,
    size_bytes: int = len(STEM_BYTES),
    sha256: str = STEM_SHA256,
    object_key: str | None = None,
) -> VerifiedADTOFStemObject:
    """Return HeadObject evidence for one canonical private drums WAV."""

    return VerifiedADTOFStemObject(
        bucket_name="clouddsp-uploads",
        object_key=object_key or f"stems/{JOB_ID}/drums.wav",
        content_type="audio/wav",
        size_bytes=size_bytes,
        sha256=sha256,
    )


class ADTOFStemDownloadTests(unittest.TestCase):
    """Prove ADTOF sees only exact verified bytes inside its context scope."""

    def test_streams_hashes_and_cleans_up_one_private_wav(self) -> None:
        """The generated scratch child disappears once the caller leaves scope."""

        body = FakeBody([STEM_BYTES[:7], STEM_BYTES[7:], b""])
        client = FakeGetObjectClient(
            {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": body}
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with downloaded_verified_adtof_stem(
                client,
                source=verified_stem(),
                work_directory=work_directory,
            ) as downloaded:
                local_path = downloaded.stem_path
                self.assertEqual(downloaded.size_bytes, len(STEM_BYTES))
                self.assertEqual(downloaded.sha256, STEM_SHA256)
                self.assertEqual(local_path.name, "stem.wav")
                self.assertEqual(local_path.read_bytes(), STEM_BYTES)
                self.assertEqual(local_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(local_path.parent.parent, work_directory.resolve())
                self.assertTrue(local_path.parent.name.startswith("adtof-stem-"))

            self.assertFalse(local_path.exists())
            self.assertEqual(list(work_directory.iterdir()), [])

        self.assertEqual(
            client.calls,
            [{"Bucket": "clouddsp-uploads", "Key": f"stems/{JOB_ID}/drums.wav"}],
        )
        self.assertTrue(body.closed)
        self.assertTrue(all(size <= ADTOF_STEM_DOWNLOAD_CHUNK_BYTES for size in body.read_sizes))

    def test_header_stream_or_sha_disagreement_never_yields_a_local_wav(self) -> None:
        """An object changed after HeadObject must be reverified, never analyzed."""

        cases = (
            (
                {"ContentLength": len(STEM_BYTES) - 1, "ContentType": "audio/wav", "Body": FakeBody([STEM_BYTES])},
                verified_stem(),
                "declared-length",
            ),
            (
                {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": FakeBody([STEM_BYTES[:-1]])},
                verified_stem(),
                "short-body",
            ),
            (
                {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": FakeBody([STEM_BYTES + b"!"])},
                verified_stem(),
                "grown-body",
            ),
            (
                {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": FakeBody([STEM_BYTES])},
                verified_stem(sha256="b" * 64),
                "sha256",
            ),
            (
                {"ContentLength": len(STEM_BYTES), "ContentType": "audio/mpeg", "Body": FakeBody([STEM_BYTES])},
                verified_stem(),
                "content-type",
            ),
        )
        for response, source, name in cases:
            with self.subTest(name=name):
                client = FakeGetObjectClient(response)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    work_directory = Path(temporary_directory)
                    with self.assertRaises(ADTOFStemDownloadConsistencyError):
                        with downloaded_verified_adtof_stem(
                            client,
                            source=source,
                            work_directory=work_directory,
                        ):
                            self.fail("Changed private input must not be yielded.")
                    self.assertEqual(list(work_directory.iterdir()), [])

    def test_storage_outage_is_retryable_and_creates_no_scratch_data(self) -> None:
        """A transient GetObject failure stays distinct from unsuitable audio."""

        client = FakeGetObjectClient(RuntimeError("private fake diagnostic"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(ADTOFStemDownloadUnavailable):
                with downloaded_verified_adtof_stem(
                    client,
                    source=verified_stem(),
                    work_directory=work_directory,
                ):
                    self.fail("Unavailable MinIO must not yield a local WAV.")
            self.assertEqual(list(work_directory.iterdir()), [])

    def test_invalid_response_stream_or_evidence_is_rejected_without_retaining_bytes(self) -> None:
        """Malformed S3 data and forged HeadObject results cannot reach ADTOF."""

        malformed_responses = (
            {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav"},
            {"ContentLength": True, "ContentType": "audio/wav", "Body": FakeBody([STEM_BYTES])},
            {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": FakeBody(["not-bytes"])},
            {
                "ContentLength": len(STEM_BYTES),
                "ContentType": "audio/wav",
                "Body": FakeBody([b"x" * (ADTOF_STEM_DOWNLOAD_CHUNK_BYTES + 1)]),
            },
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                client = FakeGetObjectClient(response)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    work_directory = Path(temporary_directory)
                    with self.assertRaises(ADTOFStemDownloadProtocolError):
                        with downloaded_verified_adtof_stem(
                            client,
                            source=verified_stem(),
                            work_directory=work_directory,
                        ):
                            self.fail("Malformed storage data must not be yielded.")
                    self.assertEqual(list(work_directory.iterdir()), [])

    def test_bad_work_directory_or_evidence_does_not_call_minio(self) -> None:
        """Scratch/evidence validation always happens before GetObject begins."""

        body = FakeBody([STEM_BYTES])
        client = FakeGetObjectClient(
            {"ContentLength": len(STEM_BYTES), "ContentType": "audio/wav", "Body": body}
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            work_directory.mkdir()
            symlink_directory = root / "linked-work"
            symlink_directory.symlink_to(work_directory, target_is_directory=True)

            with self.assertRaises(ADTOFStemDownloadProtocolError):
                with downloaded_verified_adtof_stem(
                    client,
                    source=verified_stem(sha256="not-a-sha256"),
                    work_directory=work_directory,
                ):
                    self.fail("Forged evidence must not be yielded.")
            with self.assertRaises(ADTOFStemDownloadProtocolError):
                with downloaded_verified_adtof_stem(
                    client,
                    source=verified_stem(object_key=f"stems/{JOB_ID}/nested/drums.wav"),
                    work_directory=work_directory,
                ):
                    self.fail("A nested object key must not be yielded.")
            with self.assertRaises(ADTOFStemDownloadProtocolError):
                with downloaded_verified_adtof_stem(
                    client,
                    source=verified_stem(),
                    work_directory=symlink_directory,
                ):
                    self.fail("A symlinked scratch root must not be used.")

        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
