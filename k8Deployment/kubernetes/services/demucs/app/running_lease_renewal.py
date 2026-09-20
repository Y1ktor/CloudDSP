"""Renew one already-committed Demucs ``running`` lease safely.

``task_maintenance.py`` provides the short token-guarded PostgreSQL renewal
transaction for a task identifier and lease token. This adapter restricts that
generic capability to a real ``DemucsRunningSource``: a caller cannot request a
model-attempt renewal merely by assembling IDs before the earlier
``leased -> running`` transaction committed.

Each call makes exactly one short renewal decision. A new expiry returns an
updated immutable running-source value with the same task identity, token,
source evidence, and model-start timestamp. A no-row result is normal ownership
loss: the task expired, another worker recovered it, or it became inactive.
The later process coordinator must stop before further model/artifact work in
that case.

This module intentionally owns no clock, timer thread, process handle, or
signal operation. It does not execute Demucs, contact MinIO/RabbitMQ, sleep,
recover a due task, classify a failure, write a result, or change an image,
Deployment, or KEDA. The execution workspace uses this one-shot durable result
at bounded process checkpoints without holding a transaction across CPU work.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.preflight_task_start import DemucsRunningSource
from app.task_lease import DEFAULT_DEMUCS_LEASE_SECONDS
from app.task_maintenance import DemucsTaskMaintenanceDatabase, renew_one_demucs_task_lease


class DemucsRunningLeaseRenewalOutcome(StrEnum):
    """The only durable facts a future process coordinator may act upon."""

    # PostgreSQL confirmed the same active token and provided a fresh expiry.
    RENEWED = "renewed"
    # PostgreSQL found no matching live task/token; stop the stale attempt.
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True)
class DemucsRunningLeaseRenewal:
    """One committed renewal result without a cursor, client, or error detail.

    ``running`` is available only for a confirmed renewal and contains a new
    immutable lease value with the database-returned expiry. The original
    source evidence is deliberately preserved by identity: renewal changes
    ownership timing, not the admitted source file or its model configuration.
    """

    outcome: DemucsRunningLeaseRenewalOutcome
    running: DemucsRunningSource | None = None

    def __post_init__(self) -> None:
        """Make a lost lease unable to masquerade as model authority."""

        if not isinstance(self.outcome, DemucsRunningLeaseRenewalOutcome):
            raise TypeError("Demucs running lease-renewal outcome is invalid.")
        has_running = self.running is not None
        requires_running = self.outcome is DemucsRunningLeaseRenewalOutcome.RENEWED
        if has_running != requires_running:
            raise ValueError("Demucs running lease-renewal evidence does not match its outcome.")
        if has_running and not isinstance(self.running, DemucsRunningSource):
            raise TypeError("Demucs running lease-renewal evidence is invalid.")


def renew_running_demucs_lease(
    *,
    database: DemucsTaskMaintenanceDatabase,
    running: DemucsRunningSource,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
) -> DemucsRunningLeaseRenewal:
    """Commit one renewal for the current running source, or report ownership loss.

    The underlying SQL requires the exact task UUID, current token, active
    status, and unexpired database-clock lease. Therefore a returned `RENEWED`
    result proves only that PostgreSQL accepted this worker's current ownership;
    it does not prove a model child succeeded or grant permission to skip later
    completion guards. `OWNERSHIP_LOST` is safe normal control flow and must
    cause the later coordinator to terminate work rather than retry a stale
    token locally.

    A database outage or malformed SQL return remains an exception. Hiding it
    as a successful renewal would let an unknown lease expire during CPU work,
    while hiding it as ownership loss would discard useful retryable operator
    evidence. No clock/timer/process work occurs inside this short call.
    """

    if not isinstance(running, DemucsRunningSource):
        raise TypeError("running must be DemucsRunningSource.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")

    renewed_expiry = renew_one_demucs_task_lease(
        database=database,
        task_id=running.lease.task_id,
        lease_token=running.lease.lease_token,
        lease_seconds=lease_seconds,
    )
    if renewed_expiry is None:
        return DemucsRunningLeaseRenewal(
            outcome=DemucsRunningLeaseRenewalOutcome.OWNERSHIP_LOST,
        )

    # Keep every immutable source/model coordinate unchanged. The later
    # coordinator receives updated expiry evidence without being able to swap
    # task IDs, tokens, source metadata, or the original started-at fact.
    return DemucsRunningLeaseRenewal(
        outcome=DemucsRunningLeaseRenewalOutcome.RENEWED,
        running=replace(
            running,
            lease=replace(running.lease, lease_expires_at=renewed_expiry),
        ),
    )
