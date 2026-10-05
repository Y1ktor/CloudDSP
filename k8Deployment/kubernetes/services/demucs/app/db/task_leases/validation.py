"""Validate shared lease settings and dictionary-row evidence before use.

Database driver results are not permission to process audio until their shape,
identifiers, attempt count, and timezone-aware expiry have passed these guards.
These helpers neither execute SQL nor coerce malformed values into authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Callable
from uuid import UUID, uuid4

from .contracts import (
    MAX_DEMUCS_LEASE_SECONDS,
    MAX_DEMUCS_TASK_ATTEMPTS,
    MIN_DEMUCS_LEASE_SECONDS,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
)


def _canonical_uuid(value: object) -> str:
    """Require canonical lower-case UUID text from message, database, or factory."""

    if not isinstance(value, str):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.") from error
    if normalized != value:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return normalized


def _new_canonical_uuid(factory: Callable[[], UUID] = uuid4) -> str:
    """Generate an explicit application UUID rather than relying on DB defaults."""

    generated = factory()
    if not isinstance(generated, UUID):
        raise DemucsTaskLeaseProtocolError("Demucs task identifier factory is invalid.")
    return str(generated)


def _validated_lease_seconds(lease_seconds: int) -> int:
    """Keep lease duration in the contract's bounded range before SQL binding."""

    if type(lease_seconds) is not int or not MIN_DEMUCS_LEASE_SECONDS <= lease_seconds <= MAX_DEMUCS_LEASE_SECONDS:
        raise DemucsTaskLeaseProtocolError("Demucs task lease duration is invalid.")
    return lease_seconds


def _mapping_or_error(row: Mapping[str, object] | None) -> Mapping[str, object]:
    """Reject a driver row shape the adapter cannot safely interpret."""

    if not isinstance(row, Mapping):
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return row


def _row_text(row: Mapping[str, object], name: str) -> str:
    """Read a safe non-empty text field from a dictionary cursor row."""

    value = row.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return value


def _row_attempt_count(row: Mapping[str, object]) -> int:
    """Validate PostgreSQL's bounded task-attempt counter before using it."""

    value = row.get("attempt_count")
    if type(value) is not int or not 1 <= value <= MAX_DEMUCS_TASK_ATTEMPTS:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return value


def _row_lease(row: Mapping[str, object]) -> DemucsTaskLease:
    """Convert a returned newly owned lease row into immutable application data."""

    if _row_text(row, "status") != "leased":
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    lease_expires_at = row.get("lease_expires_at")
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return DemucsTaskLease(
        task_id=_canonical_uuid(_row_text(row, "task_id")),
        job_id=_canonical_uuid(_row_text(row, "job_id")),
        request_event_id=_canonical_uuid(_row_text(row, "request_event_id")),
        input_bucket=_row_text(row, "input_bucket"),
        input_object_key=_row_text(row, "input_object_key"),
        stem_mode=_row_text(row, "stem_mode"),
        attempt_count=_row_attempt_count(row),
        lease_token=_canonical_uuid(_row_text(row, "lease_token")),
        lease_expires_at=lease_expires_at,
    )
