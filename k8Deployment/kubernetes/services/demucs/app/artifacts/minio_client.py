"""Restricted Boto3-compatible MinIO client configuration for Demucs.

The source-preflight composition needs only S3 ``HeadObject`` and ``GetObject``
calls. This module converts the future Demucs Pod's reviewed non-secret
configuration and namespace-local Secret values into one explicit path-style
S3 client. The client is suitable for MinIO because MinIO exposes the S3 API;
it does not contact AWS or require AWS credentials.

The Boto3 import is deliberately lazy. Unit tests can validate all settings and
factory arguments without installing an SDK or opening a network connection.
The later dependency-lock/image task will install the reviewed Boto3/Botocore
versions before any real worker executes this factory.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlparse

from app.processing.source_preflight import DemucsSourcePreflightClient


# These non-secret values are set explicitly in the future Deployment. Keeping
# the expected bucket/region here lets the worker reject a typo or an attempt
# to use browser-facing routing before it constructs an S3 client.
LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
LOCAL_S3_REGION = "us-east-1"
DEMUCS_MINIO_CONNECT_TIMEOUT_SECONDS = 5
DEMUCS_MINIO_READ_TIMEOUT_SECONDS = 60
DEMUCS_MINIO_MAX_ATTEMPTS = 3


class DemucsMinioConfigurationError(RuntimeError):
    """A required local MinIO setting/SDK dependency is absent or unsafe.

    Every message names only an environment-variable or component category.
    It never includes an endpoint value, S3 access key, MinIO secret key, or
    arbitrary SDK diagnostic that might accidentally reach a worker log.
    """


@dataclass(frozen=True)
class DemucsMinioSettings:
    """The narrow S3 configuration injected into a future Demucs Pod.

    The endpoint is intentionally the internal ClusterIP Service DNS name,
    never `minio.localhost` through Traefik. The two credential fields are
    excluded from ``repr`` so an accidental settings debug statement cannot
    serialize values mounted from `clouddsp-demucs-minio-credentials`.
    """

    internal_endpoint: str
    uploads_bucket: str
    region_name: str
    addressing_style: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "DemucsMinioSettings":
        """Read the future Deployment's local-only MinIO configuration.

        This method performs no network I/O. Its strict endpoint check prevents
        a Pod from accidentally routing private media through a host port or
        browser Ingress, while the bucket/region/style checks retain the fixed
        local storage contract used by the Demucs IAM policy and preflight.
        """

        endpoint = _required_environment_text("DEMUCS_S3_INTERNAL_ENDPOINT")
        _validate_internal_service_endpoint(endpoint)
        uploads_bucket = _required_environment_text("DEMUCS_S3_UPLOADS_BUCKET")
        if uploads_bucket != LOCAL_UPLOADS_BUCKET:
            raise DemucsMinioConfigurationError(
                "DEMUCS_S3_UPLOADS_BUCKET must match the local Demucs uploads bucket."
            )
        region_name = _required_environment_text("DEMUCS_S3_REGION")
        if region_name != LOCAL_S3_REGION:
            raise DemucsMinioConfigurationError(
                "DEMUCS_S3_REGION must match the local S3 region."
            )
        addressing_style = _required_environment_text("DEMUCS_S3_ADDRESSING_STYLE")
        if addressing_style != "path":
            # One internal Service owns every bucket route; virtual-host bucket
            # names would require per-bucket DNS that this cluster does not use.
            raise DemucsMinioConfigurationError("DEMUCS_S3_ADDRESSING_STYLE must be path.")
        return cls(
            internal_endpoint=endpoint,
            uploads_bucket=uploads_bucket,
            region_name=region_name,
            addressing_style=addressing_style,
            access_key=_required_environment_text("DEMUCS_S3_ACCESS_KEY"),
            secret_key=_required_environment_text("DEMUCS_S3_SECRET_KEY"),
        )


def _required_environment_text(name: str) -> str:
    """Return non-empty configuration without ever echoing its mounted value."""

    value = os.environ.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsMinioConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _validate_internal_service_endpoint(endpoint: str) -> None:
    """Require a simple HTTP(S) origin on Kubernetes Service DNS ending in `.svc`."""

    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(".svc")
    ):
        raise DemucsMinioConfigurationError(
            "DEMUCS_S3_INTERNAL_ENDPOINT must be a simple Kubernetes Service DNS origin ending in .svc."
        )


def _load_boto3_client_factories() -> tuple[Callable[..., object], Callable[..., object]]:
    """Load SDK construction functions only when the real worker starts.

    Keeping imports here means tests and pure contract code remain runnable
    before the future immutable image installs the pinned requirements lock.
    """

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise DemucsMinioConfigurationError("Pinned Demucs S3 client dependency is unavailable.") from error
    return boto3.client, Config


def create_boto3_demucs_minio_client(settings: DemucsMinioSettings) -> DemucsSourcePreflightClient:
    """Create one explicit, private path-style MinIO S3 client.

    Boto3 uses the supplied MinIO access/secret key directly, so it cannot
    probe ambient AWS environment variables, EC2 metadata, or a host profile.
    Signature V4 and path-style addressing match MinIO's S3 API behind one
    ClusterIP Service. Connection/read timeouts and standard retry attempts
    bound a future worker's storage dependency; stream-level size validation
    remains the responsibility of the source-download adapter.
    """

    if not isinstance(settings, DemucsMinioSettings):
        raise TypeError("settings must be DemucsMinioSettings.")
    boto3_client, config_factory = _load_boto3_client_factories()
    config = config_factory(
        signature_version="s3v4",
        connect_timeout=DEMUCS_MINIO_CONNECT_TIMEOUT_SECONDS,
        read_timeout=DEMUCS_MINIO_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "max_attempts": DEMUCS_MINIO_MAX_ATTEMPTS},
        s3={"addressing_style": settings.addressing_style},
    )
    return cast(
        DemucsSourcePreflightClient,
        boto3_client(
            "s3",
            endpoint_url=settings.internal_endpoint,
            region_name=settings.region_name,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            config=config,
        ),
    )
