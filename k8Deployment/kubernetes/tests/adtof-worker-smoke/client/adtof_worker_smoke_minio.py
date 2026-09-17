"""Construct one restricted path-style Boto3 MinIO client for the future smoke run.

MinIO implements the S3 protocol locally, so this factory signs requests for
the private in-cluster MinIO Service; it does not contact AWS or obtain AWS
credentials. The future smoke identity's MinIO policy, plus the existing
fixed-key adapters, decide which of the three smoke objects may be read,
written, or deleted.

This source-only factory does not call MinIO, PostgreSQL, RabbitMQ, or ADTOF;
it does not run the workflow, sleep, build an image, or create a Kubernetes
Job. Boto3 imports are lazy, and Boto3 client construction itself only builds
local configuration. A later entrypoint will call this factory once and inject
the returned client into the existing input/output/cleanup boundaries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, cast

from adtof_worker_smoke_contract import (
    EXPECTED_S3_ACCESS_KEY,
    MINIO_REGION,
    PRIVATE_MINIO_ENDPOINT_URL,
    ADTOFWorkerSmokeSettings,
)


# These bounds govern a single MinIO HTTP operation by the future smoke client.
# They are not a worker retry policy: the state machine reports a failed action
# and leaves durable/object evidence available for diagnosis instead of retrying.
ADTOF_WORKER_SMOKE_MINIO_CONNECT_TIMEOUT_SECONDS = 3
ADTOF_WORKER_SMOKE_MINIO_READ_TIMEOUT_SECONDS = 10
ADTOF_WORKER_SMOKE_MINIO_MAX_ATTEMPTS = 2
ADTOF_WORKER_SMOKE_MINIO_ADDRESSING_STYLE = "path"


class ADTOFWorkerSmokeMinIOClient(Protocol):
    """The union of S3 operations already narrowed by the three smoke adapters."""

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Read metadata for one policy-authorized fixed object."""

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Open one policy-authorized fixed output stream."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Store only the input adapter's controlled fixed WAV."""

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Delete only the cleanup adapter's verified fixed evidence."""


class ADTOFWorkerSmokeMinIOConfigurationError(RuntimeError):
    """Reject a forged MinIO route/identity/settings shape before Boto3 exists."""


class ADTOFWorkerSmokeMinIODependencyError(RuntimeError):
    """The future image omitted the reviewed hash-pinned Boto3/Botocore closure."""


class ADTOFWorkerSmokeMinIOInfrastructureError(RuntimeError):
    """Hide concrete SDK construction diagnostics from a future smoke Job log."""


def _valid_secret_text(value: object) -> bool:
    """Accept one non-empty control-free secret without returning/logging its value."""

    return isinstance(value, str) and bool(value) and "\x00" not in value


def _validated_settings(value: object) -> ADTOFWorkerSmokeSettings:
    """Revalidate direct data-class construction before credentials reach Boto3.

    The environment loader already constrains endpoint, region, and access-key
    identity. This repeated check matters because callers can directly create a
    frozen settings object in Python and otherwise redirect a restricted key to
    another S3-compatible endpoint without changing a Kubernetes manifest.
    """

    if (
        not isinstance(value, ADTOFWorkerSmokeSettings)
        or value.minio_endpoint_url != PRIVATE_MINIO_ENDPOINT_URL
        or value.minio_region != MINIO_REGION
        or value.minio_access_key != EXPECTED_S3_ACCESS_KEY
        or not _valid_secret_text(value.minio_secret_key)
    ):
        raise ADTOFWorkerSmokeMinIOConfigurationError("ADTOF smoke MinIO settings are invalid.")
    return value


def _load_boto3_client_factories() -> tuple[Callable[..., object], Callable[..., object]]:
    """Import the locked Boto3/Botocore pair only when a client is requested."""

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise ADTOFWorkerSmokeMinIODependencyError(
            "Pinned ADTOF smoke S3 dependency is unavailable."
        ) from error
    return boto3.client, Config


def create_boto3_adtof_worker_smoke_minio_client(
    settings: ADTOFWorkerSmokeSettings,
) -> ADTOFWorkerSmokeMinIOClient:
    """Build one explicit private, path-style S3 client without an object request.

    Explicit credentials prevent Boto3 from consulting host AWS profiles,
    ambient credential variables, or EC2 metadata. Signature V4, fixed region,
    and path addressing match the local MinIO Service; neither a browser
    ingress nor a host-forwarded endpoint can be substituted through settings.
    """

    approved = _validated_settings(settings)
    boto3_client, config_factory = _load_boto3_client_factories()
    try:
        config = config_factory(
            signature_version="s3v4",
            connect_timeout=ADTOF_WORKER_SMOKE_MINIO_CONNECT_TIMEOUT_SECONDS,
            read_timeout=ADTOF_WORKER_SMOKE_MINIO_READ_TIMEOUT_SECONDS,
            retries={"mode": "standard", "max_attempts": ADTOF_WORKER_SMOKE_MINIO_MAX_ATTEMPTS},
            s3={"addressing_style": ADTOF_WORKER_SMOKE_MINIO_ADDRESSING_STYLE},
        )
        return cast(
            ADTOFWorkerSmokeMinIOClient,
            boto3_client(
                "s3",
                endpoint_url=approved.minio_endpoint_url,
                region_name=approved.minio_region,
                aws_access_key_id=approved.minio_access_key,
                aws_secret_access_key=approved.minio_secret_key,
                config=config,
            ),
        )
    except Exception as error:  # SDK construction details remain private to the future runtime.
        raise ADTOFWorkerSmokeMinIOInfrastructureError(
            "ADTOF smoke MinIO client is unavailable."
        ) from error
