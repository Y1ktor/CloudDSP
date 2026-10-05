"""Gate Demucs source preflight behind a successfully acknowledged task lease.

``amqp_manual_ack.py`` finishes the one-delivery broker decision first.  Only
its ``ACKNOWLEDGED_LEASE`` result proves both of the following facts:

* PostgreSQL committed the canonical Demucs task lease; and
* RabbitMQ accepted the acknowledgement of the request that caused that lease.

This small adapter is the handoff from that durable/broker boundary to the
already-built MinIO-and-FFprobe source-preflight boundary.  It intentionally
does not receive AMQP frames, open a database transaction, transition task
state, retry work, start a loop, invoke a Demucs model, or create Kubernetes
resources.  Those decisions remain separate so a later worker supervisor can
make them explicit and testable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.processing.ffprobe_process import DemucsFFprobeRunner
from app.processing.source_preflight import (
    DemucsSourcePreflightClient,
    OpenedValidatedDemucsSourceWorkspace,
    ValidatedDemucsSource,
    opened_validated_demucs_source_workspace,
    validate_claimed_demucs_source,
)
from app.db.task_lease import DemucsTaskLease


class DemucsAcknowledgedLeasePreflightError(RuntimeError):
    """Raised when a normal receive outcome is not eligible for source work.

    This fixed, non-sensitive category is a programming/control-flow guard,
    not a retry decision.  An idle queue, duplicate, stale request, or malformed
    request carries no task ownership and must never cause a MinIO request or
    FFprobe process.  A future supervisor handles those outcomes before calling
    this function rather than treating this exception as audio-processing work.
    """


@dataclass(frozen=True)
class DemucsAcknowledgedLeasePreflight:
    """The exact lease and its source evidence for the next guarded step.

    Keeping the lease alongside the durable-safe source evidence prevents a
    later state-transition/model layer from losing the PostgreSQL lease token it
    must present when it changes the task.  The evidence itself contains no
    temporary local file path; ``source_preflight`` removes source bytes before
    it returns.
    """

    lease: DemucsTaskLease
    source: ValidatedDemucsSource


@dataclass(frozen=True)
class DemucsAcknowledgedLeaseSourceWorkspace:
    """A broker-acknowledged lease plus a temporary model-eligible source path.

    The ``source_path`` has no meaning after the surrounding context manager
    exits. ``preflight`` deliberately returns only the durable-safe lease and
    source evidence needed by the existing guarded `leased -> running`
    transaction; it never carries the local path into PostgreSQL.
    """

    lease: DemucsTaskLease
    source: ValidatedDemucsSource
    source_path: Path

    def __post_init__(self) -> None:
        """Keep durable evidence and ephemeral media handoff shapes explicit."""

        if not isinstance(self.lease, DemucsTaskLease):
            raise TypeError("Demucs acknowledged source workspace lease is invalid.")
        if not isinstance(self.source, ValidatedDemucsSource):
            raise TypeError("Demucs acknowledged source workspace evidence is invalid.")
        if not isinstance(self.source_path, Path):
            raise TypeError("Demucs acknowledged source workspace path is invalid.")

    @property
    def preflight(self) -> DemucsAcknowledgedLeasePreflight:
        """Return the existing durable-safe handoff without exposing a local path."""

        return DemucsAcknowledgedLeasePreflight(lease=self.lease, source=self.source)


def _acknowledged_lease_or_raise(receive_result: DemucsConsumeOneResult) -> DemucsTaskLease:
    """Extract the one lease that may start storage/media work after broker ack."""

    if not isinstance(receive_result, DemucsConsumeOneResult):
        raise TypeError("receive_result must be DemucsConsumeOneResult.")
    if receive_result.outcome is not DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        raise DemucsAcknowledgedLeasePreflightError(
            "Demucs source preflight requires an acknowledged task lease."
        )

    # The result dataclass enforces this pairing at construction. Retaining the
    # guard protects this boundary if unsafe integration code or a test double
    # bypasses normal construction and loses the token after an acknowledgement.
    lease = receive_result.lease
    if lease is None:
        raise DemucsAcknowledgedLeasePreflightError(
            "Demucs source preflight requires an acknowledged task lease."
        )
    return lease


@contextmanager
def opened_acknowledged_demucs_source_workspace(
    receive_result: DemucsConsumeOneResult,
    client: DemucsSourcePreflightClient,
    *,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
) -> Iterator[DemucsAcknowledgedLeaseSourceWorkspace]:
    """Yield one verified local source only after its task lease was acknowledged.

    This is the future model handoff. It reuses the narrow source workspace
    boundary, but adds the non-negotiable manual-ack guard: idle, duplicate,
    stale, and malformed delivery outcomes cannot call MinIO, start FFprobe,
    or obtain a local media path. The scope ends before any later loop can
    receive another broker message, ensuring one worker owns at most one local
    source file under its existing ``prefetch=1`` policy.
    """

    lease = _acknowledged_lease_or_raise(receive_result)
    with opened_validated_demucs_source_workspace(
        client,
        lease=lease,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
    ) as workspace:
        # Keep this narrow structural check at the boundary so an unsafe future
        # replacement context manager cannot fabricate a lease/path pairing.
        if not isinstance(workspace, OpenedValidatedDemucsSourceWorkspace):
            raise TypeError("Demucs source workspace is invalid.")
        yield DemucsAcknowledgedLeaseSourceWorkspace(
            lease=lease,
            source=workspace.validated_source,
            source_path=workspace.source_path,
        )


def preflight_acknowledged_demucs_lease(
    receive_result: DemucsConsumeOneResult,
    client: DemucsSourcePreflightClient,
    *,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
) -> DemucsAcknowledgedLeasePreflight:
    """Run source preflight only for one acknowledged, newly acquired lease.

    The ``DemucsConsumeOneResult`` constructor already prevents a normal result
    from pairing the wrong outcome with a lease.  This explicit branch still
    makes the next runtime layer's permission visible at the handoff point: no
    result other than ``ACKNOWLEDGED_LEASE`` may contact MinIO or start FFprobe.
    Storage and probe exceptions deliberately propagate unchanged so a later
    lease-token-guarded result adapter can classify permanent media faults and
    retryable dependency faults without losing their meaning.
    """

    lease = _acknowledged_lease_or_raise(receive_result)

    source = validate_claimed_demucs_source(
        client,
        lease=lease,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
    )
    return DemucsAcknowledgedLeasePreflight(lease=lease, source=source)
