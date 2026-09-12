"""Commit a verified Basic Pitch stem lease before exposing it to model work.

This context manager is the narrow handoff between three already isolated
boundaries:

1. ``stem_download.py`` gives a temporary local WAV only after MinIO metadata,
   streamed byte count, and SHA-256 verification.
2. ``task_lease.py`` owns the pure lease-token guarded ``leased`` → ``running``
   SQL statement.
3. ``postgresql.py`` supplies one short commit-or-rollback transaction.

The model-eligible local path is yielded only after the transaction commits.
It stays valid for the caller's context scope and is then removed by the
download boundary. This module does not receive/ack RabbitMQ, renew a lease,
invoke Basic Pitch, upload MIDI, write a result, or use Kubernetes APIs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.stem_download import (
    BasicPitchGetObjectClient,
    DownloadedBasicPitchStem,
    downloaded_verified_basic_pitch_stem,
)
from app.stem_object import VerifiedBasicPitchStemObject
from app.task_lease import DatabaseCursor, BasicPitchTaskLease, start_leased_basic_pitch_task


class BasicPitchTaskStartCompositionError(RuntimeError):
    """A verified-stem/lease pair cannot form a safe model-start handoff."""


class BasicPitchTaskStartDatabase(Protocol):
    """The only PostgreSQL capability needed for the one start-state decision."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a dictionary-row cursor in a short commit-or-rollback scope."""


@dataclass(frozen=True)
class RunningBasicPitchStem:
    """A committed running lease and its temporary verified local WAV.

    The path exists only while :func:`started_verified_basic_pitch_stem` yields
    this value. A future model runner must consume it inside that scope and
    retain only new MIDI evidence—not the source path—after it exits.
    """

    lease: BasicPitchTaskLease
    stem: DownloadedBasicPitchStem
    started_at: datetime


def _matching_verified_stem(
    *,
    lease: object,
    source: object,
) -> tuple[BasicPitchTaskLease, VerifiedBasicPitchStemObject]:
    """Prevent a valid private stem for one task becoming input for another task."""

    if not isinstance(lease, BasicPitchTaskLease):
        raise TypeError("lease must be BasicPitchTaskLease.")
    if not isinstance(source, VerifiedBasicPitchStemObject):
        raise TypeError("source must be VerifiedBasicPitchStemObject.")
    if source.bucket_name != lease.input_bucket or source.object_key != lease.input_object_key:
        raise BasicPitchTaskStartCompositionError(
            "Basic Pitch verified stem does not match the claimed task."
        )
    return lease, source


@contextmanager
def started_verified_basic_pitch_stem(
    *,
    database: BasicPitchTaskStartDatabase,
    client: BasicPitchGetObjectClient,
    lease: BasicPitchTaskLease,
    source: VerifiedBasicPitchStemObject,
    work_directory: Path,
) -> Iterator[RunningBasicPitchStem | None]:
    """Yield model input only after one committed current-lease start transition.

    The MinIO download begins while the task remains ``leased``. That allows a
    later result policy to distinguish input failures from model failures. Once
    its complete local hash proof exists, a short PostgreSQL transaction checks
    that this worker still owns the same unexpired lease and commits ``running``.

    On ownership loss, this context yields ``None`` only *after* the temporary
    WAV has been removed, so the caller cannot accidentally process stale data.
    Database/storage/lease exceptions propagate unchanged after cleanup; the
    later supervisor decides retry, terminal result, and broker policy.
    """

    matched_lease, matched_source = _matching_verified_stem(lease=lease, source=source)
    with downloaded_verified_basic_pitch_stem(
        client,
        source=matched_source,
        work_directory=work_directory,
    ) as downloaded_stem:
        # The `with` ends before the caller receives the local path. Therefore
        # a normal non-None yield is only possible after the transaction has
        # committed the same token that preflight/download used.
        with database.write_cursor() as cursor:
            started_at = start_leased_basic_pitch_task(cursor, lease=matched_lease)

        if started_at is not None:
            yield RunningBasicPitchStem(
                lease=matched_lease,
                stem=downloaded_stem,
                started_at=started_at,
            )
            return

    # Exiting the download context removed the source before the caller sees
    # normal ownership loss, keeping stale data unavailable to model code.
    yield None
