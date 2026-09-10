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

from dataclasses import dataclass
from pathlib import Path

from app.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.ffprobe_process import DemucsFFprobeRunner
from app.source_preflight import (
    DemucsSourcePreflightClient,
    ValidatedDemucsSource,
    validate_claimed_demucs_source,
)
from app.task_lease import DemucsTaskLease


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

    if receive_result.outcome is not DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE:
        raise DemucsAcknowledgedLeasePreflightError(
            "Demucs source preflight requires an acknowledged task lease."
        )

    # The result dataclass enforces this pairing at construction. Retaining the
    # defensive guard protects this boundary if an unsafe object is supplied by
    # future integration code or a test double bypasses normal construction.
    lease = receive_result.lease
    if lease is None:
        raise DemucsAcknowledgedLeasePreflightError(
            "Demucs source preflight requires an acknowledged task lease."
        )

    source = validate_claimed_demucs_source(
        client,
        lease=lease,
        work_directory=work_directory,
        ffprobe_runner=ffprobe_runner,
    )
    return DemucsAcknowledgedLeasePreflight(lease=lease, source=source)
