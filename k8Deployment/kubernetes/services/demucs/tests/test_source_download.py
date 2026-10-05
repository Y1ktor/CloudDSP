"""Unit tests for bounded private Demucs source download and cleanup.

The fake client/body below never contacts MinIO. Tests prove exact-byte
streaming and temporary-directory cleanup without PostgreSQL, RabbitMQ,
FFprobe, Demucs, Kubernetes, or a cloud SDK.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.artifacts.source_download import (
    DEMUCS_SOURCE_DOWNLOAD_CHUNK_BYTES,
    DemucsSourceDownloadConsistencyError,
    DemucsSourceDownloadProtocolError,
    DemucsSourceDownloadUnavailable,
    downloaded_verified_demucs_source,
)
from app.artifacts.source_object import VerifiedDemucsSourceObject


class FakeBody:
    """A small closeable streaming body with observable bounded read requests."""

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
    """Record only the exact private object request expected by the adapter."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> object:
        self.calls.append({"Bucket": Bucket, "Key": Key})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def verified_source(*, size_bytes: int = 6) -> VerifiedDemucsSourceObject:
    """Return already-HeadObject-verified private coordinates for one task."""

    return VerifiedDemucsSourceObject(
        bucket_name="clouddsp-uploads",
        object_key="uploads/08ec1d44-3106-4fcb-91c8-5d0c78e7e046/mix.wav",
        content_type="audio/wav",
        size_bytes=size_bytes,
    )


class DemucsSourceDownloadTests(unittest.TestCase):
    """Prove only exact verified bytes are temporarily available to FFprobe."""

    def test_streams_exact_private_bytes_to_generated_file_then_cleans_up(self) -> None:
        """The random child directory disappears after the caller leaves its scope."""

        body = FakeBody([b"ab", b"cdef", b""])
        client = FakeGetObjectClient({"ContentLength": 6, "Body": body})
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with downloaded_verified_demucs_source(
                client,
                source=verified_source(),
                work_directory=work_directory,
            ) as downloaded:
                local_path = downloaded.source_path
                self.assertEqual(downloaded.size_bytes, 6)
                self.assertEqual(local_path.name, "source.media")
                self.assertEqual(local_path.read_bytes(), b"abcdef")
                self.assertEqual(local_path.stat().st_mode & 0o777, 0o600)
                # macOS exposes /var through a /private/var alias; compare the
                # canonical work-directory identity used by the adapter.
                self.assertEqual(local_path.parent.parent, work_directory.resolve())
                self.assertTrue(local_path.parent.name.startswith("demucs-source-"))

            self.assertFalse(local_path.exists())
            self.assertEqual(list(work_directory.iterdir()), [])

        self.assertEqual(
            client.calls,
            [{"Bucket": "clouddsp-uploads", "Key": verified_source().object_key}],
        )
        self.assertTrue(body.closed)
        self.assertTrue(all(size <= DEMUCS_SOURCE_DOWNLOAD_CHUNK_BYTES for size in body.read_sizes))

    def test_declared_or_streamed_size_disagreement_never_yields_a_file(self) -> None:
        """A changed object must be reverified rather than analysed as the old object."""

        cases = (
            (5, [b"abcdef"], "declared-length-mismatch"),
            (6, [b"abc"], "short-body"),
            (6, [b"abcdefg"], "grown-body"),
        )
        for response_length, chunks, name in cases:
            with self.subTest(name=name):
                body = FakeBody(chunks)
                client = FakeGetObjectClient({"ContentLength": response_length, "Body": body})
                with tempfile.TemporaryDirectory() as temporary_directory:
                    work_directory = Path(temporary_directory)
                    with self.assertRaises(DemucsSourceDownloadConsistencyError):
                        with downloaded_verified_demucs_source(
                            client,
                            source=verified_source(),
                            work_directory=work_directory,
                        ):
                            self.fail("A mismatched source must not be yielded.")
                    self.assertEqual(list(work_directory.iterdir()), [])
                self.assertTrue(body.closed)

    def test_storage_outage_is_retryable_and_does_not_create_scratch_files(self) -> None:
        """Transport failure remains distinct from unsuitable media evidence."""

        client = FakeGetObjectClient(RuntimeError("private fake diagnostic"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(DemucsSourceDownloadUnavailable):
                with downloaded_verified_demucs_source(
                    client,
                    source=verified_source(),
                    work_directory=work_directory,
                ):
                    self.fail("An unavailable object store must not yield a file.")
            self.assertEqual(list(work_directory.iterdir()), [])

    def test_invalid_response_or_stream_is_rejected_without_retaining_partial_bytes(self) -> None:
        """Client-shape bugs cannot become an FFprobe input file."""

        cases = (
            {"ContentLength": 6},
            {"ContentLength": True, "Body": FakeBody([b"abcdef"])},
            {"ContentLength": 6, "Body": FakeBody(["not-bytes"])},
            {"ContentLength": 6, "Body": FakeBody([b"a" * (DEMUCS_SOURCE_DOWNLOAD_CHUNK_BYTES + 1)])},
        )
        for response in cases:
            with self.subTest(response_type=type(response).__name__):
                client = FakeGetObjectClient(response)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    work_directory = Path(temporary_directory)
                    with self.assertRaises(DemucsSourceDownloadProtocolError):
                        with downloaded_verified_demucs_source(
                            client,
                            source=verified_source(),
                            work_directory=work_directory,
                        ):
                            self.fail("Malformed storage data must not be yielded.")
                    self.assertEqual(list(work_directory.iterdir()), [])

    def test_bad_work_directory_or_evidence_does_not_call_minio(self) -> None:
        """Scratch and evidence validation both happen before GetObject begins."""

        body = FakeBody([b"abcdef"])
        client = FakeGetObjectClient({"ContentLength": 6, "Body": body})
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "work"
            work_directory.mkdir()
            symlink_directory = root / "linked-work"
            symlink_directory.symlink_to(work_directory, target_is_directory=True)

            with self.assertRaises(DemucsSourceDownloadProtocolError):
                with downloaded_verified_demucs_source(
                    client,
                    source=verified_source(size_bytes=0),
                    work_directory=work_directory,
                ):
                    self.fail("Invalid evidence must not be yielded.")
            with self.assertRaises(DemucsSourceDownloadProtocolError):
                with downloaded_verified_demucs_source(
                    client,
                    source=verified_source(),
                    work_directory=symlink_directory,
                ):
                    self.fail("A symlinked scratch root must not be yielded.")

        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
