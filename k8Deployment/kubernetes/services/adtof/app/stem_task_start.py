"""Commit a verified ADTOF drums lease before exposing it to model work.

This context manager joins three isolated boundaries in the required order:

1. ``stem_download.py`` returns a temporary WAV only after metadata, streamed
   byte-count, and SHA-256 verification.
2. ``task_claim.py`` owns the pure lease-token guarded ``leased`` → ``running``
   SQL transition.
3. ``postgresql.py`` supplies a short commit-or-rollback transaction.

The model-eligible local path is yielded only after the transaction commits and
only while the download context remains open. This module does not receive or
acknowledge RabbitMQ, renew a lease, invoke ADTOF, upload artifacts, write a
result, build an image, or access Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.stem_download import (
    ADTOFGetObjectClient,
    DownloadedADTOFStem,
    downloaded_verified_adtof_stem,
)
from app.stem_object import VerifiedADTOFStemObject
from app.task_claim import DatabaseCursor, ADTOFTaskLease, start_leased_adtof_task


class ADTOFTaskStartCompositionError(RuntimeError):
    """A verified drums object/lease pair cannot form a safe model handoff."""


class ADTOFTaskStartDatabase(Protocol):
    """The one PostgreSQL capability needed for the start-state decision."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a dictionary-row cursor inside a short commit/rollback scope."""


@dataclass(frozen=True)
class RunningADTOFStem:
    """A committed running ADTOF lease and its temporary verified drums WAV.

    The path exists only while :func:`started_verified_adtof_stem` yields this
    value. A later model adapter must consume it within that scope and retain
    only newly produced MIDI/tempo evidence after the context cleans scratch.
    """

    lease: ADTOFTaskLease
    stem: DownloadedADTOFStem
    started_at: datetime


def _matching_verified_stem(
    *,
    lease: object,
    source: object,
) -> tuple[ADTOFTaskLease, VerifiedADTOFStemObject]:
    """Prevent valid private drums input for one task crossing into another."""

    if not isinstance(lease, ADTOFTaskLease):
        raise TypeError("lease must be ADTOFTaskLease.")
    if not isinstance(source, VerifiedADTOFStemObject):
        raise TypeError("source must be VerifiedADTOFStemObject.")
    if source.bucket_name != lease.input_bucket or source.object_key != lease.input_object_key:
        raise ADTOFTaskStartCompositionError(
            "ADTOF verified stem does not match the claimed task."
        )
    return lease, source


@contextmanager
def started_verified_adtof_stem(
    *,
    database: ADTOFTaskStartDatabase,
    client: ADTOFGetObjectClient,
    lease: ADTOFTaskLease,
    source: VerifiedADTOFStemObject,
    work_directory: Path,
) -> Iterator[RunningADTOFStem | None]:
    """Yield ADTOF model input only after one committed current-lease transition.

    The MinIO download occurs while the task remains ``leased``. That lets a
    later lifecycle policy distinguish storage/input failures from model
    failures. Once complete local hash proof exists, a short transaction checks
    the same unexpired token and commits ``running`` before the caller can see
    the path.

    Ownership loss yields ``None`` only *after* the temporary WAV has been
    removed, so a stale replica cannot process it. Database, storage, or lease
    exceptions propagate unchanged after cleanup; a later supervisor owns retry
    timing, terminal outcomes, and broker policy.
    """

    matched_lease, matched_source = _matching_verified_stem(lease=lease, source=source)
    with downloaded_verified_adtof_stem(
        client,
        source=matched_source,
        work_directory=work_directory,
    ) as downloaded_stem:
        # This transaction begins only once metadata/download/checksum proof
        # exists. A non-None path is therefore impossible until the same lease
        # token's start transition has left the context normally (committed).
        with database.write_cursor() as cursor:
            started_at = start_leased_adtof_task(cursor, lease=matched_lease)

        if started_at is not None:
            yield RunningADTOFStem(
                lease=matched_lease,
                stem=downloaded_stem,
                started_at=started_at,
            )
            return

    # The download scope removes the private path before normal ownership loss
    # becomes visible to the caller, preventing accidental stale model work.
    yield None
