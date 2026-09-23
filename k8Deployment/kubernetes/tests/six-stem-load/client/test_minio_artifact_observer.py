"""Offline proof for 42 exact MinIO Head/Get and streamed SHA-256 checks."""

from __future__ import annotations

import hashlib
import unittest
from uuid import UUID

from minio_artifact_observer import (
    MinIOArtifactObserver,
    MinIOArtifactObserverError,
    _ADTOF_MODEL_CONFIGURATION_ID,
)
from minio_observer_bootstrap import TemporaryMinIOObserverCredentials
from postgresql_durable_state_observer import snapshot_from_aggregate_row
from test_postgresql_durable_state_observer import aggregate_row, _RUN_MARKER


_UUID = "00000000-0000-0000-0000-000000000099"
_STEMS = ("bass", "drums", "guitar", "other", "piano", "vocals")
_BASIC_PITCH_STEMS = ("bass", "vocals", "other", "guitar", "piano")


class _Body:
    """Minimal stream object matching the boto3 Body read/close surface."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk = self.content[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeMinIO:
    """Serve only explicitly prepared object coordinates and count SDK calls."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.head_calls: list[str] = []
        self.get_calls: list[str] = []
        self.last_body: _Body | None = None

    def add(self, key: str, content: bytes, *, content_type: str, metadata: dict[str, str]) -> None:
        self.objects[key] = (content, metadata, content_type)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.head_calls.append(Key)
        content, metadata, content_type = self.objects[Key]
        return {"ContentLength": len(content), "ContentType": content_type, "Metadata": metadata}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.get_calls.append(Key)
        content, _, _ = self.objects[Key]
        self.last_body = _Body(content)
        return {"Body": self.last_body}


def _fixture() -> tuple[object, _FakeMinIO]:
    """Pair a deterministic validated DB manifest with matching private objects."""

    row = list(aggregate_row())
    manifests = row[10]
    client = _FakeMinIO()
    for ordinal, job in enumerate(manifests, start=1):
        job_id = job["job_id"]
        source_key = job["input_object_key"]
        source_bytes = f"tiny-source-{ordinal}".encode()
        job["source_size_bytes"] = len(source_bytes)
        job["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        client.add(source_key, source_bytes, content_type="audio/wav", metadata={})

        stem_hashes: dict[str, str] = {}
        for stem_name, stem in job["stems"].items():
            key = stem["object_key"]
            content = f"{job_id}-{stem_name}".encode()
            sha256 = hashlib.sha256(content).hexdigest()
            stem["size_bytes"] = len(content)
            stem["sha256"] = sha256
            stem_hashes[stem_name] = sha256
            client.add(
                key,
                content,
                content_type="audio/wav",
                metadata={
                    "schema-version": "1",
                    "producer": "demucs",
                    "job-id": job_id,
                    "stem-name": stem_name,
                    "stem-mode": "6-stems",
                    "size-bytes": str(len(content)),
                    "sha256": sha256,
                },
            )

        for stem_name in _BASIC_PITCH_STEMS:
            key = f"midi/{job_id}/{stem_name}.mid"
            content = f"midi-{job_id}-{stem_name}".encode()
            sha256 = hashlib.sha256(content).hexdigest()
            client.add(
                key,
                content,
                content_type="audio/midi",
                metadata={
                    "schema-version": "1",
                    "producer": "basic-pitch",
                    "job-id": job_id,
                    "stem-name": stem_name,
                    "stem-mode": "6-stems",
                    "input-stem-sha256": stem_hashes[stem_name],
                    "task-id": str(UUID(_UUID)),
                    "request-event-id": str(UUID(_UUID)),
                    "size-bytes": str(len(content)),
                    "sha256": sha256,
                },
            )

        for key, artifact_kind, content_type in (
            (job["midi"]["drums"]["s3_key"], "drum-midi", "audio/midi"),
            (job["midi"]["drums"]["bpm_key"], "tempo-candidate", "application/json"),
        ):
            content = f"{artifact_kind}-{job_id}".encode()
            sha256 = hashlib.sha256(content).hexdigest()
            client.add(
                key,
                content,
                content_type=content_type,
                metadata={
                    "schema-version": "1",
                    "producer": "adtof",
                    "job-id": job_id,
                    "stem-name": "drums",
                    "stem-mode": "6-stems",
                    "input-stem-sha256": stem_hashes["drums"],
                    "task-id": str(UUID(_UUID)),
                    "request-event-id": str(UUID(_UUID)),
                    "artifact-kind": artifact_kind,
                    "model-config-id": _ADTOF_MODEL_CONFIGURATION_ID,
                    "size-bytes": str(len(content)),
                    "sha256": sha256,
                },
            )
    return snapshot_from_aggregate_row(tuple(row), run_marker=_RUN_MARKER), client


class MinIOArtifactObserverTests(unittest.TestCase):
    """Reject changed object bytes, metadata, keys, and partial inventories."""

    def test_hashes_exactly_42_objects_without_bucket_listing(self) -> None:
        snapshot, client = _fixture()
        observer = MinIOArtifactObserver(
            credentials=TemporaryMinIOObserverCredentials(
                run_marker=_RUN_MARKER,
                access_key=f"s6m{_RUN_MARKER}",
                secret_key="S" * 48,
            ),
            client_factory=lambda _credentials: client,
        )

        result = observer.verify_all(snapshot=snapshot)

        self.assertEqual((result.object_count, result.source_object_count), (42, 3))
        self.assertEqual((result.stem_object_count, result.midi_object_count), (18, 21))
        self.assertEqual(len(client.head_calls), 42)
        self.assertEqual(client.head_calls, client.get_calls)
        self.assertTrue(client.last_body and client.last_body.closed)

    def test_detects_changed_bytes_even_if_content_length_is_unchanged(self) -> None:
        snapshot, client = _fixture()
        key = next(key for key in client.objects if key.startswith("uploads/"))
        # Replacing the first source body preserves length but invalidates the
        # source SHA supplied by the authenticated test coordinates.
        content, metadata, content_type = client.objects[key]
        client.objects[key] = (b"X" * len(content), metadata, content_type)
        observer = MinIOArtifactObserver(
            credentials=TemporaryMinIOObserverCredentials(
                run_marker=_RUN_MARKER,
                access_key=f"s6m{_RUN_MARKER}",
                secret_key="S" * 48,
            ),
            client_factory=lambda _credentials: client,
        )
        with self.assertRaises(MinIOArtifactObserverError):
            observer.verify_all(snapshot=snapshot)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
