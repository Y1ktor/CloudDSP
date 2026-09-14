"""Restricted Boto3-compatible MinIO client configuration for ADTOF.

This module turns a future ADTOF Pod's fixed non-secret configuration and its
namespace-local least-privilege MinIO Secret into one private path-style S3
client. MinIO implements the S3 protocol, so Boto3 talks to the local MinIO
Service; it does not contact AWS or obtain AWS credentials.

Imports are deliberately lazy. Contract tests run before a later image task
installs the hash-pinned Boto3 dependency, and client construction itself opens
no network connection. A later HeadObject verifier will inject the returned
client and decide whether one claimed drums stem may be read.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast


# These are immutable local deployment contracts rather than arbitrary worker
# knobs. The MinIO identity policy narrows which objects it may access, while
# these constants prevent the Pod configuration from redirecting the identity
# through a browser Ingress, a host port, another service, or another bucket.
LOCAL_MINIO_INTERNAL_ENDPOINT = "http://clouddsp-minio.clouddsp-data.svc:9000"
LOCAL_ARTIFACTS_BUCKET = "clouddsp-uploads"
LOCAL_S3_REGION = "us-east-1"
LOCAL_S3_ADDRESSING_STYLE = "path"

# These bounds limit one future worker's dependency on storage. They are not a
# task retry policy: task leases and PostgreSQL schedule durable retries later.
ADTOF_MINIO_CONNECT_TIMEOUT_SECONDS = 5
ADTOF_MINIO_READ_TIMEOUT_SECONDS = 60
ADTOF_MINIO_MAX_ATTEMPTS = 3


class ADTOFMinioConfigurationError(RuntimeError):
    """A MinIO setting or pinned SDK dependency is absent or outside contract.

    Public messages name only an environment variable or component category.
    They never include endpoints, access keys, secret keys, or raw SDK errors
    that a future worker might otherwise put into an ordinary Pod log.
    """


class ADTOFMinioClient(Protocol):
    """The narrow S3 surface available to later ADTOF storage adapters.

    This protocol causes no request. It documents the policy-compatible calls:
    metadata/stream reads for the exact drums stem and metadata/object writes
    for deterministic drums MIDI and tempo objects. The policy, task lease, and
    later object adapters—not this generic client—decide which concrete keys
    are safe for any particular call.
    """

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Read metadata for one authorized private object without its bytes."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Open one authorized private object stream in a later narrow adapter."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Write one deterministic MIDI/tempo artifact after later validation."""


@dataclass(frozen=True)
class ADTOFMinioSettings:
    """The complete S3 configuration available to a future ADTOF Pod.

    The Secret-derived key pair uses ``repr=False`` as defence in depth. An
    accidental debug rendering of this settings value cannot serialize either
    mounted credential even though an ADTOF image later needs them to sign its
    private MinIO requests.
    """

    internal_endpoint: str
    artifacts_bucket: str
    region_name: str
    addressing_style: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "ADTOFMinioSettings":
        """Read and validate future Deployment/Secret values without MinIO I/O.

        Exact equality rejects a browser Ingress, `localhost`, a different
        internal Service, a foreign bucket, or virtual-host S3 addressing
        before Boto3 exists. The Deployment will set the non-secret values and
        mount only the restricted `clouddsp-adtof` MinIO credential pair.
        """

        return validate_adtof_minio_settings(
            cls(
                internal_endpoint=_required_environment_text("ADTOF_S3_INTERNAL_ENDPOINT"),
                artifacts_bucket=_required_environment_text("ADTOF_S3_ARTIFACTS_BUCKET"),
                region_name=_required_environment_text("ADTOF_S3_REGION"),
                addressing_style=_required_environment_text("ADTOF_S3_ADDRESSING_STYLE"),
                access_key=_required_environment_text("ADTOF_S3_ACCESS_KEY"),
                secret_key=_required_environment_text("ADTOF_S3_SECRET_KEY"),
            )
        )


def _required_environment_text(name: str) -> str:
    """Read non-empty control-free configuration text without echoing it."""

    value = os.environ.get(name)
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ADTOFMinioConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _safe_direct_text(value: object) -> bool:
    """Recognize direct settings text that cannot bypass environment checks."""

    return (
        isinstance(value, str)
        and bool(value)
        and not any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    )


def validate_adtof_minio_settings(value: object) -> ADTOFMinioSettings:
    """Revalidate direct dataclass construction before Boto3 can sign a request.

    Frozen dataclasses are useful in tests and future composition code, but
    callers can construct one without ``from_environment()``. Repeating the
    endpoint/bucket/region/path-style and credential-shape checks means a
    restricted key pair cannot be redirected to an arbitrary S3-compatible
    server simply by bypassing the Deployment environment loader.
    """

    if not isinstance(value, ADTOFMinioSettings):
        raise TypeError("settings must be ADTOFMinioSettings.")
    if (
        value.internal_endpoint != LOCAL_MINIO_INTERNAL_ENDPOINT
        or value.artifacts_bucket != LOCAL_ARTIFACTS_BUCKET
        or value.region_name != LOCAL_S3_REGION
        or value.addressing_style != LOCAL_S3_ADDRESSING_STYLE
        or not _safe_direct_text(value.access_key)
        or not _safe_direct_text(value.secret_key)
    ):
        raise ADTOFMinioConfigurationError("ADTOF MinIO settings are outside the local contract.")
    return value


def _load_boto3_client_factories() -> tuple[Callable[..., object], Callable[..., object]]:
    """Load Boto3 only when a real worker composes its client.

    The later requirements-lock/image task supplies reviewed Boto3 and Botocore
    versions. Unit tests patch this loader, so they require no package install,
    Service DNS lookup, MinIO request, or external network access.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise ADTOFMinioConfigurationError(
            "Pinned ADTOF S3 client dependency is unavailable."
        ) from error
    return boto3.client, Config


def create_boto3_adtof_minio_client(settings: ADTOFMinioSettings) -> ADTOFMinioClient:
    """Construct one private MinIO client without making an object request.

    Passing MinIO's restricted access/secret key directly prevents Boto3 from
    consulting ambient AWS variables, host profiles, or EC2 metadata. SigV4,
    fixed private origin, and path-style addressing match the local MinIO S3
    service. This client has no task authority by itself: later PostgreSQL-
    guarded object adapters must still supply one claimed drums coordinate.
    """

    approved = validate_adtof_minio_settings(settings)
    boto3_client, config_factory = _load_boto3_client_factories()
    config = config_factory(
        signature_version="s3v4",
        connect_timeout=ADTOF_MINIO_CONNECT_TIMEOUT_SECONDS,
        read_timeout=ADTOF_MINIO_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "max_attempts": ADTOF_MINIO_MAX_ATTEMPTS},
        s3={"addressing_style": approved.addressing_style},
    )
    return cast(
        ADTOFMinioClient,
        boto3_client(
            "s3",
            endpoint_url=approved.internal_endpoint,
            region_name=approved.region_name,
            aws_access_key_id=approved.access_key,
            aws_secret_access_key=approved.secret_key,
            config=config,
        ),
    )
