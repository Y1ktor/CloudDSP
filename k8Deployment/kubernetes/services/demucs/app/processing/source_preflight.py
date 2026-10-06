"""One lease-bound source-admission composition for the future Demucs worker.

This is intentionally the narrow point where the three completed source
boundaries meet:

1. ``HeadObject`` proves the claimed task's private coordinate and metadata.
2. ``GetObject`` streams the same verified-size object into Pod-local scratch.
3. FFprobe proves the resulting bytes contain audio within the duration limit.

It returns evidence only after the temporary source file has been removed. The
future worker still owns PostgreSQL transaction/lease transitions, RabbitMQ
manual acknowledgement, MinIO client construction, model invocation, stem
upload, and all terminal/retry decisions.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.processing.audio_probe import VerifiedDemucsAudioProbe
from app.processing.ffprobe_process import DemucsFFprobeRunner, run_verified_demucs_audio_probe
from app.artifacts.source_download import DemucsGetObjectClient, downloaded_verified_demucs_source
from app.artifacts.source_object import (
    DemucsHeadObjectClient,
    VerifiedDemucsSourceObject,
    verify_claimed_demucs_source_head_object,
)
from app.db.task_lease import DemucsTaskLease


class DemucsSourcePreflightClient(DemucsHeadObjectClient, DemucsGetObjectClient, Protocol):
    """The combined least-privilege S3 surface used for one source preflight.

    A later composition root will inject one Boto3-compatible MinIO client
    constructed with the restricted Demucs identity. This protocol deliberately
    exposes only ``HeadObject`` and ``GetObject``; it cannot list, delete,
    presign, or administer buckets.
    """


@dataclass(frozen=True)
class ValidatedDemucsSource:
    """Durable-safe source evidence retained after ephemeral scratch is removed.

    It includes the verified private coordinate/size and FFprobe's audio/duration
    evidence, but never a local file path. A caller may use it to make a later
    guarded task-state decision while keeping all local-source lifecycle inside
    this function's short preflight scope.
    """

    source_object: VerifiedDemucsSourceObject
    audio_probe: VerifiedDemucsAudioProbe


@dataclass(frozen=True)
class OpenedValidatedDemucsSourceWorkspace:
    """One verified source path that exists only inside a caller's ``with`` scope.

    ``validated_source`` is durable-safe HeadObject/FFprobe evidence; its
    companion ``source_path`` is intentionally ephemeral Pod-local data. The
    context manager below owns the random scratch child and removes it after
    the caller returns or raises. A future Demucs process must therefore run
    *inside* that scope and must never persist or log ``source_path``.
    """

    validated_source: ValidatedDemucsSource
    source_path: Path

    def __post_init__(self) -> None:
        """Reject a malformed handoff before it reaches a model command builder."""

        if not isinstance(self.validated_source, ValidatedDemucsSource):
            raise TypeError("Demucs source workspace requires validated source evidence.")
        if not isinstance(self.source_path, Path):
            raise TypeError("Demucs source workspace path is invalid.")


@contextmanager
def opened_validated_demucs_source_workspace(
    client: DemucsSourcePreflightClient,
    *,
    lease: DemucsTaskLease,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
) -> Iterator[OpenedValidatedDemucsSourceWorkspace]:
    """Open one verified local source only for a bounded caller-owned operation.

    This is the reusable source-admission/workspace boundary:

    ``HeadObject -> exact streaming GetObject -> FFprobe -> yield local path``.

    The yielded path remains below the existing Pod scratch mount only until
    the ``with`` block ends. The underlying download context then closes the
    S3 body and deletes its random child directory on success, on a model
    failure, or when an outer worker begins graceful shutdown. No database
    state, RabbitMQ action, model command, artifact write, or retry decision is
    made here; callers retain those distinct durable responsibilities.
    """

    verified_object = verify_claimed_demucs_source_head_object(client, lease=lease)
    with downloaded_verified_demucs_source(
        client,
        source=verified_object,
        work_directory=work_directory,
    ) as downloaded:
        audio_probe = run_verified_demucs_audio_probe(
            source_path=downloaded.source_path,
            work_directory=work_directory,
            runner=ffprobe_runner,
        )
        yield OpenedValidatedDemucsSourceWorkspace(
            validated_source=ValidatedDemucsSource(
                source_object=verified_object,
                audio_probe=audio_probe,
            ),
            source_path=downloaded.source_path,
        )


def validate_claimed_demucs_source(
    client: DemucsSourcePreflightClient,
    *,
    lease: DemucsTaskLease,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
) -> ValidatedDemucsSource:
    """Validate one claimed source through MinIO metadata, bytes, and FFprobe.

    This function makes exactly one metadata-only ``HeadObject`` call followed
    by one streaming ``GetObject`` call only after HeadObject succeeds. The
    temporary download is available to FFprobe solely inside the context block;
    by the time this function returns, raises, or hands control to a caller,
    that temporary file is gone. Exceptions from the individual layers are
    preserved rather than collapsed, so the future token-guarded result adapter
    can distinguish a permanent bad-media category from a retryable storage or
    FFprobe process condition.
    """

    # Keep the established evidence-only helper for callers that deliberately
    # must not retain a local path. The new workspace API above is the separate
    # future model handoff: it exposes the same source only inside its caller's
    # context manager, then uses this identical cleanup boundary.
    with opened_validated_demucs_source_workspace(
        client,
        lease=lease,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
    ) as workspace:
        return workspace.validated_source
