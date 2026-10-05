"""Configuration-only boundary for the Job API's future MinIO/S3 client.

The Job API needs two S3 endpoint names because one URL cannot correctly serve
both network audiences in this local Kubernetes design:

* ``internal_endpoint`` is Kubernetes Service DNS. A Pod uses it for a future
  real S3 request, such as reading object metadata, without leaving cluster
  networking.
* ``public_endpoint`` is the Traefik address a Mac browser can reach. Future
  presigned POST/GET generation embeds this URL in a signed response. Signing
  is a local calculation and must not try to connect from the Pod to
  ``*.localhost``.

This module deliberately creates no SDK client and opens no socket. The
separate presigned_upload module owns the reviewed local Signature V4
calculation; it consumes these validated settings without turning configuration
parsing into a network side effect. Keeping settings parsing isolated lets unit
tests validate the Kubernetes environment contract independently.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


# S3 bucket names used in this local project follow AWS-compatible DNS naming.
# Limiting the parser to this predictable form avoids a future presigned URL
# accidentally gaining an ambiguous path, uppercase host, or invalid bucket
# component. This is intentionally a configuration constraint, not browser
# input validation; the POST /jobs route validates request fields separately.
_S3_BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_S3_REGION_PATTERN = re.compile(r"^[a-z0-9-]{2,64}$")


class ObjectStorageConfigurationError(RuntimeError):
    """Raised when required MinIO settings are absent or unsafe to use.

    Error messages name only the invalid *environment variable*. They never
    interpolate access keys, secret keys, endpoint query strings, or any other
    potentially sensitive configuration value.
    """


def required_environment_value(name: str) -> str:
    """Return one non-empty environment setting without exposing its value."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ObjectStorageConfigurationError(
            f"Required environment variable {name} is absent."
        )
    return value.strip()


def required_endpoint_url(*, name: str, require_service_dns: bool) -> str:
    """Validate one simple HTTP(S) endpoint used in signed S3 configuration.

    The API intentionally supports only origin-style endpoints here—scheme,
    host, and optional port—with no path/query/fragment/user info. The Traefik
    Ingress preserves the full S3 path, so a future presigned URL must be able
    to append ``/bucket/key`` itself rather than signing through a proxy path
    prefix. Internal endpoints must use Kubernetes Service DNS; public signing
    endpoints must not, because a browser cannot resolve ``*.svc``.
    """

    value = required_environment_value(name)
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ObjectStorageConfigurationError(
            f"{name} must be a simple absolute HTTP(S) origin URL."
        )

    hostname = parsed.hostname
    if hostname is None:
        raise ObjectStorageConfigurationError(
            f"{name} must be a simple absolute HTTP(S) origin URL."
        )

    uses_service_dns = hostname.endswith(".svc")
    if require_service_dns and not uses_service_dns:
        raise ObjectStorageConfigurationError(
            f"{name} must use Kubernetes Service DNS ending in .svc."
        )
    if not require_service_dns and uses_service_dns:
        raise ObjectStorageConfigurationError(
            f"{name} must be browser-reachable and must not use .svc DNS."
        )

    # Normalize an optional harmless trailing slash away. This keeps future
    # signature construction independent of whether a ConfigMap author typed
    # the origin with or without the one trailing separator.
    return value.rstrip("/")


def required_bucket_name(name: str) -> str:
    """Validate the one durable source-upload bucket name."""

    value = required_environment_value(name)
    if not _S3_BUCKET_NAME_PATTERN.fullmatch(value) or ".." in value:
        raise ObjectStorageConfigurationError(
            f"{name} must be a 3-63 character lowercase S3 bucket name."
        )
    return value


def required_region(name: str) -> str:
    """Validate the non-secret region included in Signature Version 4 requests."""

    value = required_environment_value(name)
    if not _S3_REGION_PATTERN.fullmatch(value):
        raise ObjectStorageConfigurationError(
            f"{name} must contain only lowercase letters, digits, and hyphens."
        )
    return value


@dataclass(frozen=True)
class ObjectStorageSettings:
    """Restricted MinIO settings injected into the Job API Pod.

    Access and secret keys are omitted from ``repr``. This protects against an
    accidental debug log of the settings object while retaining ordinary,
    inspectable field names for code and unit tests.
    """

    internal_endpoint: str
    public_endpoint: str
    uploads_bucket: str
    region: str
    addressing_style: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "ObjectStorageSettings":
        """Read and validate the Deployment's explicit MinIO contract.

        This method is intentionally called only by an S3-related route, not
        at module import or API startup. ``GET /jobs`` and ``/readyz`` remain
        operational even if object storage is temporarily down; ``POST /jobs``
        calls this parser only when it needs to issue an upload form.
        """

        addressing_style = required_environment_value("JOB_API_S3_ADDRESSING_STYLE")
        if addressing_style != "path":
            raise ObjectStorageConfigurationError(
                "JOB_API_S3_ADDRESSING_STYLE must be path for the local S3 Ingress."
            )

        return cls(
            internal_endpoint=required_endpoint_url(
                name="JOB_API_S3_INTERNAL_ENDPOINT",
                require_service_dns=True,
            ),
            public_endpoint=required_endpoint_url(
                name="JOB_API_S3_PUBLIC_ENDPOINT",
                require_service_dns=False,
            ),
            uploads_bucket=required_bucket_name("JOB_API_S3_UPLOADS_BUCKET"),
            region=required_region("JOB_API_S3_REGION"),
            addressing_style=addressing_style,
            access_key=required_environment_value("JOB_API_S3_ACCESS_KEY"),
            secret_key=required_environment_value("JOB_API_S3_SECRET_KEY"),
        )
