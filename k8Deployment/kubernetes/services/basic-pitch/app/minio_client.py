"""Restricted Boto3-compatible MinIO client configuration for Basic Pitch.

This module turns a future Basic Pitch Pod's reviewed non-secret configuration
and its namespace-local, least-privilege MinIO Secret into one private S3
client. MinIO implements the S3 protocol, so Boto3 is an S3 protocol client in
this local cluster; it does not contact AWS or obtain AWS credentials.

The import is intentionally lazy. The pure request/lease tests can run before
the later image task installs the pinned Boto3 dependency, and calling this
factory alone performs no network I/O. A later HeadObject verifier will inject
the returned narrow client and decide whether the claimed stem can be read.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast


# These are reviewed local deployment constants, not arbitrary worker knobs.
# Basic Pitch is allowed to use just the private ClusterIP Service, one bucket,
# and S3 path-style addressing. Keeping the values fixed means a compromised
# ConfigMap cannot repoint a restricted credential at a different endpoint.
LOCAL_MINIO_INTERNAL_ENDPOINT = "http://clouddsp-minio.clouddsp-data.svc:9000"
LOCAL_ARTIFACTS_BUCKET = "clouddsp-uploads"
LOCAL_S3_REGION = "us-east-1"
LOCAL_S3_ADDRESSING_STYLE = "path"

# The connection limits bound one future worker's storage dependency. They do
# not retry a claimed task indefinitely: PostgreSQL lease/retry policy owns
# the durable scheduling decision in a later adapter.
BASIC_PITCH_MINIO_CONNECT_TIMEOUT_SECONDS = 5
BASIC_PITCH_MINIO_READ_TIMEOUT_SECONDS = 60
BASIC_PITCH_MINIO_MAX_ATTEMPTS = 3


class BasicPitchMinioConfigurationError(RuntimeError):
    """A required MinIO setting or the pinned SDK is absent or unsafe.

    Messages name only a setting/category. They never include endpoints,
    access keys, secret keys, or raw SDK diagnostics that could leak through a
    future worker log.
    """


class BasicPitchMinioClient(Protocol):
    """The complete but narrow S3 surface granted by the Basic Pitch policy.

    Defining this protocol does not make a request. It documents the methods a
    later verifier/download/upload layer may receive: metadata/object reads
    for `stems/*`, and metadata/object writes for deterministic `midi/*` keys.
    The MinIO policy, durable task lease, and those future adapters together
    determine which particular call is safe.
    """

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Read metadata for one already-authorized object without its bytes."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Return one already-authorized private object stream in a later layer."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Write one deterministic MIDI artifact in a later guarded layer."""


@dataclass(frozen=True)
class BasicPitchMinioSettings:
    """The only S3 configuration a future Basic Pitch Pod may receive.

    The credential values use ``repr=False``. This is defence in depth for a
    Kubernetes Secret injected as environment variables: an accidental normal
    debug rendering of settings cannot serialize either secret value.
    """

    internal_endpoint: str
    artifacts_bucket: str
    region_name: str
    addressing_style: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "BasicPitchMinioSettings":
        """Read and strictly validate future Deployment/Secret environment values.

        No value is sent to MinIO here. The explicit equality checks reject a
        browser Ingress, localhost host-port, another internal Service, another
        bucket, and virtual-host S3 routing before a Boto3 client exists.
        """

        endpoint = _required_environment_text("BASIC_PITCH_S3_INTERNAL_ENDPOINT")
        if endpoint != LOCAL_MINIO_INTERNAL_ENDPOINT:
            raise BasicPitchMinioConfigurationError(
                "BASIC_PITCH_S3_INTERNAL_ENDPOINT must match the local private MinIO Service."
            )
        artifacts_bucket = _required_environment_text("BASIC_PITCH_S3_ARTIFACTS_BUCKET")
        if artifacts_bucket != LOCAL_ARTIFACTS_BUCKET:
            raise BasicPitchMinioConfigurationError(
                "BASIC_PITCH_S3_ARTIFACTS_BUCKET must match the local Basic Pitch artifacts bucket."
            )
        region_name = _required_environment_text("BASIC_PITCH_S3_REGION")
        if region_name != LOCAL_S3_REGION:
            raise BasicPitchMinioConfigurationError(
                "BASIC_PITCH_S3_REGION must match the local S3 region."
            )
        addressing_style = _required_environment_text("BASIC_PITCH_S3_ADDRESSING_STYLE")
        if addressing_style != LOCAL_S3_ADDRESSING_STYLE:
            raise BasicPitchMinioConfigurationError(
                "BASIC_PITCH_S3_ADDRESSING_STYLE must be path."
            )
        return cls(
            internal_endpoint=endpoint,
            artifacts_bucket=artifacts_bucket,
            region_name=region_name,
            addressing_style=addressing_style,
            access_key=_required_environment_text("BASIC_PITCH_S3_ACCESS_KEY"),
            secret_key=_required_environment_text("BASIC_PITCH_S3_SECRET_KEY"),
        )


def _required_environment_text(name: str) -> str:
    """Return non-empty, control-free environment text without echoing its value."""

    value = os.environ.get(name)
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x20 or 0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise BasicPitchMinioConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _load_boto3_client_factories() -> tuple[Callable[..., object], Callable[..., object]]:
    """Load Boto3 only when a real worker composes its client.

    The later dependency-lock/image task supplies reviewed Boto3 and Botocore
    versions. Until then, this repository's pure unit tests use a patched
    factory and therefore need no dependency installation or network access.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise BasicPitchMinioConfigurationError(
            "Pinned Basic Pitch S3 client dependency is unavailable."
        ) from error
    return boto3.client, Config


def create_boto3_basic_pitch_minio_client(
    settings: BasicPitchMinioSettings,
) -> BasicPitchMinioClient:
    """Construct one explicit private MinIO S3 client without making a request.

    Passing MinIO's restricted key pair directly prevents Boto3 from probing
    ambient AWS environment variables, host profiles, or EC2 metadata. SigV4,
    path-style addressing, and the fixed ClusterIP origin match the local
    MinIO topology. The returned client has no inherent task authority: a
    later PostgreSQL-guarded verifier must still supply one claimed key.
    """

    if not isinstance(settings, BasicPitchMinioSettings):
        raise TypeError("settings must be BasicPitchMinioSettings.")
    boto3_client, config_factory = _load_boto3_client_factories()
    config = config_factory(
        signature_version="s3v4",
        connect_timeout=BASIC_PITCH_MINIO_CONNECT_TIMEOUT_SECONDS,
        read_timeout=BASIC_PITCH_MINIO_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "max_attempts": BASIC_PITCH_MINIO_MAX_ATTEMPTS},
        s3={"addressing_style": settings.addressing_style},
    )
    return cast(
        BasicPitchMinioClient,
        boto3_client(
            "s3",
            endpoint_url=settings.internal_endpoint,
            region_name=settings.region_name,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            config=config,
        ),
    )
