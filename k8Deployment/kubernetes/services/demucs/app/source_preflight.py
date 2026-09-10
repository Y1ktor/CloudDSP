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

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.audio_probe import VerifiedDemucsAudioProbe
from app.ffprobe_process import DemucsFFprobeRunner, run_verified_demucs_audio_probe
from app.source_download import DemucsGetObjectClient, downloaded_verified_demucs_source
from app.source_object import (
    DemucsHeadObjectClient,
    VerifiedDemucsSourceObject,
    verify_claimed_demucs_source_head_object,
)
from app.task_lease import DemucsTaskLease


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
    return ValidatedDemucsSource(
        source_object=verified_object,
        audio_probe=audio_probe,
    )
