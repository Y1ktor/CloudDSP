"""Shared validation for user-supplied linked-media page URLs.

The Job API and yt-dlp worker both validate the initial URL. Keeping the
provider policy here prevents a request accepted by the API from being
interpreted differently by the asynchronous worker.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit


# These are page hosts, not the media/CDN hosts that yt-dlp may use after it
# has resolved an approved page. Subdomains of an entry are permitted.
DEFAULT_ALLOWED_MEDIA_HOSTS = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "b23.tv",
    "soundcloud.com",
    "on.soundcloud.com",
)


class MediaUrlPolicyError(ValueError):
    """Raised when a linked-media URL is outside the ingestion policy."""


def configured_allowed_media_hosts() -> tuple[str, ...]:
    """Read the reviewed source-page allowlist from the Lambda environment."""
    configured = os.environ.get("ALLOWED_MEDIA_HOSTS")
    raw_hosts = configured.split(",") if configured is not None else DEFAULT_ALLOWED_MEDIA_HOSTS
    hosts = tuple(host.strip().lower().rstrip(".") for host in raw_hosts if host.strip())
    if not hosts:
        raise RuntimeError("ALLOWED_MEDIA_HOSTS must contain at least one hostname.")
    for host in hosts:
        if any(character in host for character in "/:@") or "." not in host:
            raise RuntimeError("ALLOWED_MEDIA_HOSTS must contain hostnames only.")
    return hosts


def hostname_is_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Return whether a hostname is an allowlisted host or its subdomain."""
    normalized = hostname.lower().rstrip(".")
    return any(
        normalized == allowed_host or normalized.endswith(f".{allowed_host}")
        for allowed_host in allowed_hosts
    )


def validate_allowlisted_media_url(value: Any) -> str:
    """Require one credential-free HTTPS URL for a reviewed media provider."""
    if not isinstance(value, str) or not value.strip():
        raise MediaUrlPolicyError("source_url is required.")

    source_url = value.strip()
    if len(source_url) > 2_048:
        raise MediaUrlPolicyError("source_url is too long.")

    try:
        parsed = urlsplit(source_url)
        port = parsed.port
    except ValueError as error:
        raise MediaUrlPolicyError("source_url contains an invalid port.") from error

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname:
        raise MediaUrlPolicyError("source_url must be an absolute HTTPS URL.")
    if parsed.username or parsed.password:
        raise MediaUrlPolicyError("source_url must not contain credentials.")
    if port not in {None, 443}:
        raise MediaUrlPolicyError("source_url must use HTTPS port 443.")
    if not hostname_is_allowed(hostname, configured_allowed_media_hosts()):
        raise MediaUrlPolicyError("source_url host is not an approved media provider.")
    return source_url
