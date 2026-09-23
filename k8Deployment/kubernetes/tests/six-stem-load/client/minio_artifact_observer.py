"""Verify exact six-stem load objects against PostgreSQL and S3 byte hashes.

The observer does not list a bucket or trust a presigned URL. It issues a
finite set of ``HeadObject`` and streaming ``GetObject`` requests for keys
bound by the temporary PostgreSQL function and the broker-created exact-key
MinIO policy. Source audio and Demucs stems are compared with independently
stored SHA-256 values; MIDI output checksums are compared with their own object
metadata and bytes, while their input-stem hash is cross-checked against the
durable Demucs stem hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Callable, Mapping
from uuid import UUID

from minio_observer_bootstrap import (
    MinIOObserverBootstrapError,
    TemporaryMinIOObserverCredentials,
    read_minio_observer_credentials,
)
from postgresql_durable_state_observer import (
    LoadJobArtifactEvidence,
    PostgreSQLDurableStateSnapshot,
)
from pathlib import Path


_ENDPOINT = "http://clouddsp-minio.clouddsp-data.svc:9000"
_BUCKET = "clouddsp-uploads"
_REGION = "us-east-1"
_READ_CHUNK_BYTES = 64 * 1024
_MAX_OBJECT_BYTES = 256 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_STEMS = ("bass", "vocals", "other", "guitar", "piano")
_ADTOF_MODEL_CONFIGURATION_ID = (
    "adtof-pytorch@85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9"
    ";fps=100;thresholds=0.22,0.24,0.32,0.22,0.30;device=cpu"
)


class MinIOArtifactObserverError(RuntimeError):
    """A bounded S3 verification failure without object keys or SDK details."""


@dataclass(frozen=True)
class MinIOArtifactVerification:
    """Safe counts from verifying all private source and derived objects."""

    object_count: int
    source_object_count: int
    stem_object_count: int
    midi_object_count: int


@dataclass(frozen=True)
class MinIOArtifactObserver:
    """Single-run MinIO reader with no list, write, delete, or signing operation."""

    credentials: TemporaryMinIOObserverCredentials = field(repr=False)
    client_factory: Callable[..., object] | None = field(default=None, repr=False)

    def verify_all(
        self, *, snapshot: PostgreSQLDurableStateSnapshot
    ) -> MinIOArtifactVerification:
        """Read/hash the 42 deterministic test objects and require exact counts."""

        evidence = _validated_snapshot(snapshot)
        client = (self.client_factory or _boto3_client)(self.credentials)
        source_count = stem_count = midi_count = 0
        for job in evidence:
            self._verify_object(
                client,
                object_key=job.source.object_key,
                content_type=job.source.content_type,
                expected_size=job.source.size_bytes,
                expected_sha256=job.source.sha256,
                expected_metadata=None,
                require_declared_sha256=False,
            )
            source_count += 1
            stem_hashes: dict[str, str] = {}
            for stem_name, stem in job.stems:
                self._verify_object(
                    client,
                    object_key=stem.object_key,
                    content_type=stem.content_type,
                    expected_size=stem.size_bytes,
                    expected_sha256=stem.sha256,
                    expected_metadata={
                        "schema-version": "1",
                        "producer": "demucs",
                        "job-id": job.job_id,
                        "stem-name": stem_name,
                        "stem-mode": "6-stems",
                        "size-bytes": str(stem.size_bytes),
                    },
                    require_declared_sha256=True,
                )
                assert stem.sha256 is not None
                stem_hashes[stem_name] = stem.sha256
                stem_count += 1

            for stem_name in _STEMS:
                midi_key = f"midi/{job.job_id}/{stem_name}.mid"
                self._verify_output_object(
                    client,
                    object_key=midi_key,
                    content_type="audio/midi",
                    job_id=job.job_id,
                    stem_name=stem_name,
                    producer="basic-pitch",
                    input_stem_sha256=stem_hashes[stem_name],
                )
                midi_count += 1

            drums_sha256 = stem_hashes["drums"]
            self._verify_output_object(
                client,
                object_key=job.midi_keys[0],
                content_type="audio/midi",
                job_id=job.job_id,
                stem_name="drums",
                producer="adtof",
                input_stem_sha256=drums_sha256,
                artifact_kind="drum-midi",
            )
            self._verify_output_object(
                client,
                object_key=job.midi_keys[1],
                content_type="application/json",
                job_id=job.job_id,
                stem_name="drums",
                producer="adtof",
                input_stem_sha256=drums_sha256,
                artifact_kind="tempo-candidate",
            )
            midi_count += 2
        verification = MinIOArtifactVerification(
            object_count=source_count + stem_count + midi_count,
            source_object_count=source_count,
            stem_object_count=stem_count,
            midi_object_count=midi_count,
        )
        if verification != MinIOArtifactVerification(42, 3, 18, 21):
            raise MinIOArtifactObserverError("MinIO object inventory count was invalid")
        return verification

    def _verify_output_object(
        self,
        client: object,
        *,
        object_key: str,
        content_type: str,
        job_id: str,
        stem_name: str,
        producer: str,
        input_stem_sha256: str,
        artifact_kind: str | None = None,
    ) -> None:
        """Validate producer provenance, stored digest, and the downloaded bytes."""

        response = self._head(client, object_key)
        metadata = _metadata(response.get("Metadata"))
        expected = {
            "schema-version": "1",
            "producer": producer,
            "job-id": job_id,
            "stem-name": stem_name,
            "stem-mode": "6-stems",
            "input-stem-sha256": input_stem_sha256,
        }
        if producer == "basic-pitch":
            expected.update({
                "task-id": None,
                "request-event-id": None,
            })
        else:
            expected.update({
                "task-id": None,
                "request-event-id": None,
                "artifact-kind": artifact_kind,
                "model-config-id": _ADTOF_MODEL_CONFIGURATION_ID,
            })
        for name, expected_value in expected.items():
            actual = metadata.get(name)
            if expected_value is None:
                _canonical_uuid(actual, purpose=f"{producer} {name}")
            elif actual != expected_value:
                raise MinIOArtifactObserverError(
                    "MinIO output metadata did not match its durable input"
                )
        declared_size = metadata.get("size-bytes")
        declared_sha = metadata.get("sha256")
        raw_length = response.get("ContentLength")
        if (
            type(raw_length) is not int
            or declared_size != str(raw_length)
            or not isinstance(declared_sha, str)
            or not _SHA256_PATTERN.fullmatch(declared_sha)
        ):
            raise MinIOArtifactObserverError("MinIO output integrity metadata was invalid")
        self._verify_object(
            client,
            object_key=object_key,
            content_type=content_type,
            expected_size=None,
            expected_sha256=None,
            expected_metadata={"producer": producer},
            require_declared_sha256=True,
            prefetched_head=response,
        )

    def _verify_object(
        self,
        client: object,
        *,
        object_key: str,
        content_type: str,
        expected_size: int | None,
        expected_sha256: str | None,
        expected_metadata: Mapping[str, str] | None,
        require_declared_sha256: bool,
        prefetched_head: Mapping[str, object] | None = None,
    ) -> None:
        """Head one known key, check metadata, then stream and hash every byte."""

        response = prefetched_head or self._head(client, object_key)
        length = response.get("ContentLength")
        if (
            type(length) is not int
            or not 1 <= length <= _MAX_OBJECT_BYTES
            or (expected_size is not None and length != expected_size)
            or response.get("ContentType") != content_type
        ):
            raise MinIOArtifactObserverError("MinIO object metadata did not match")
        metadata = _metadata(response.get("Metadata"))
        if expected_metadata is not None:
            for name, value in expected_metadata.items():
                if metadata.get(name) != value:
                    raise MinIOArtifactObserverError(
                        "MinIO object metadata did not match durable evidence"
                    )
        declared_sha = metadata.get("sha256")
        if require_declared_sha256:
            if not isinstance(declared_sha, str) or not _SHA256_PATTERN.fullmatch(declared_sha):
                raise MinIOArtifactObserverError("MinIO object checksum metadata was invalid")
        if expected_sha256 is not None and declared_sha is not None:
            if declared_sha != expected_sha256:
                raise MinIOArtifactObserverError(
                    "MinIO object checksum metadata did not match PostgreSQL"
                )
        try:
            result = client.get_object(Bucket=_BUCKET, Key=object_key)
            body = result.get("Body") if isinstance(result, Mapping) else None
            if body is None or not callable(getattr(body, "read", None)):
                raise MinIOArtifactObserverError("MinIO object stream was unavailable")
            digest = hashlib.sha256()
            byte_count = 0
            try:
                while chunk := body.read(_READ_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise MinIOArtifactObserverError("MinIO object stream was invalid")
                    byte_count += len(chunk)
                    if byte_count > length:
                        raise MinIOArtifactObserverError("MinIO object stream exceeded its length")
                    digest.update(chunk)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except MinIOArtifactObserverError:
            raise
        except Exception:
            # Botocore exceptions can contain the key, endpoint, or response
            # body. Emit one static category at this private service boundary.
            raise MinIOArtifactObserverError("MinIO object read failed") from None
        observed_sha = digest.hexdigest()
        if (
            byte_count != length
            or (declared_sha is not None and observed_sha != declared_sha)
            or (expected_sha256 is not None and observed_sha != expected_sha256)
        ):
            raise MinIOArtifactObserverError("MinIO object content checksum did not match")

    @staticmethod
    def _head(client: object, object_key: str) -> Mapping[str, object]:
        """Use one explicit private key; never list or sign an object URL."""

        try:
            response = client.head_object(Bucket=_BUCKET, Key=object_key)
        except Exception:
            raise MinIOArtifactObserverError("MinIO object metadata read failed") from None
        if not isinstance(response, Mapping):
            raise MinIOArtifactObserverError("MinIO object metadata response was invalid")
        return response


def verify_minio_load_artifacts(
    *,
    credential_directory: Path,
    run_marker: str,
    snapshot: PostgreSQLDurableStateSnapshot,
    client_factory: Callable[..., object] | None = None,
) -> MinIOArtifactVerification:
    """Load the temporary least-privilege key and perform full byte verification."""

    try:
        credentials = read_minio_observer_credentials(
            credential_directory, expected_run_marker=run_marker
        )
        return MinIOArtifactObserver(
            credentials=credentials,
            client_factory=client_factory,
        ).verify_all(snapshot=snapshot)
    except MinIOArtifactObserverError:
        raise
    except Exception:
        raise MinIOArtifactObserverError("MinIO observer setup failed") from None


def _metadata(value: object) -> dict[str, str]:
    """Normalize boto3's lowercase user metadata without accepting arbitrary values."""

    if not isinstance(value, Mapping):
        raise MinIOArtifactObserverError("MinIO object metadata shape was invalid")
    result: dict[str, str] = {}
    for name, content in value.items():
        if not isinstance(name, str) or not isinstance(content, str) or name.lower() in result:
            raise MinIOArtifactObserverError("MinIO object metadata shape was invalid")
        result[name.lower()] = content
    return result


def _validated_snapshot(
    snapshot: PostgreSQLDurableStateSnapshot,
) -> tuple[LoadJobArtifactEvidence, ...]:
    """Require a complete three-job/six-stem/three-ADTOF persisted manifest."""

    if (
        not isinstance(snapshot, PostgreSQLDurableStateSnapshot)
        or not isinstance(snapshot.artifact_evidence, tuple)
        or len(snapshot.artifact_evidence) != 3
    ):
        raise MinIOArtifactObserverError("PostgreSQL artifact evidence was incomplete")
    for job in snapshot.artifact_evidence:
        if (
            not isinstance(job, LoadJobArtifactEvidence)
            or len(job.stems) != 6
            or tuple(name for name, _ in job.stems) != ("bass", "drums", "guitar", "other", "piano", "vocals")
            or len(job.midi_keys) != 2
        ):
            raise MinIOArtifactObserverError("PostgreSQL artifact evidence was incomplete")
        for _, stem in job.stems:
            if (
                not isinstance(stem.sha256, str)
                or not _SHA256_PATTERN.fullmatch(stem.sha256)
                or stem.size_bytes is None
                or stem.size_bytes < 1
            ):
                raise MinIOArtifactObserverError("PostgreSQL stem evidence was incomplete")
    return snapshot.artifact_evidence


def _canonical_uuid(value: object, *, purpose: str) -> str:
    """Require UUID-shaped metadata without copying the value into diagnostics."""

    if not isinstance(value, str):
        raise MinIOArtifactObserverError(f"MinIO {purpose} metadata was invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise MinIOArtifactObserverError(f"MinIO {purpose} metadata was invalid") from None
    if value != canonical or not _UUID_PATTERN.fullmatch(value):
        raise MinIOArtifactObserverError(f"MinIO {purpose} metadata was invalid")
    return canonical


def _boto3_client(credentials: TemporaryMinIOObserverCredentials) -> object:
    """Create a path-style, endpoint-pinned client with no ambient AWS lookup."""

    try:
        import boto3
        from botocore.config import Config
        return boto3.client(
            "s3",
            endpoint_url=_ENDPOINT,
            region_name=_REGION,
            aws_access_key_id=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=3,
                read_timeout=10,
            ),
        )
    except Exception:
        raise MinIOArtifactObserverError("MinIO S3 client initialization failed") from None
