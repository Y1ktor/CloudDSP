"""Commit a validated Demucs source lease into its model-eligible running state.

This is the deliberately small composition between two completed boundaries:

1. ``acknowledged_lease_preflight.py`` provides source evidence only for an
   AMQP-acknowledged, PostgreSQL-owned lease.
2. ``task_lease.py`` provides the pure token-guarded ``leased`` → ``running``
   statement.

The function below supplies exactly one short database transaction around that
statement. Its return value is available only after the context commits, so a
future model layer never starts merely because Python constructed source
evidence—it starts only after PostgreSQL still recognizes the same lease.

It does not receive RabbitMQ deliveries, make MinIO/FFprobe calls, classify a
source failure, renew a lease, run Demucs, write artifacts, update a terminal
result, sleep, or interact with Kubernetes. Those are intentionally later
runtime decisions.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.acknowledged_lease_preflight import DemucsAcknowledgedLeasePreflight
from app.source_preflight import ValidatedDemucsSource
from app.task_lease import DatabaseCursor, DemucsTaskLease, start_leased_demucs_task


class DemucsTaskStartDatabase(Protocol):
    """The only database capability needed to commit one task-start decision.

    The concrete Psycopg adapter satisfies this structural protocol. Keeping
    connection setup outside this module lets a small in-memory context manager
    prove the commit/rollback ordering without a local PostgreSQL instance.
    """

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one dictionary-row cursor in a commit-or-rollback scope."""


@dataclass(frozen=True)
class DemucsRunningSource:
    """One committed running lease and the source evidence available to Demucs.

    The future model boundary receives the exact lease token it must renew and
    later present to result updates, plus source metadata/probe evidence. No
    temporary local path is included: source preflight has already removed the
    transient download before this object can be constructed.
    """

    lease: DemucsTaskLease
    source: ValidatedDemucsSource
    started_at: datetime


def start_preflight_validated_demucs_task(
    *,
    database: DemucsTaskStartDatabase,
    preflight: DemucsAcknowledgedLeasePreflight,
) -> DemucsRunningSource | None:
    """Commit a preflight-validated task start, or return durable ownership loss.

    A returned ``DemucsRunningSource`` proves PostgreSQL committed the
    `running` transition for the same lease token that source preflight used.
    ``None`` is a normal committed stop signal: the lease expired, another
    worker recovered the task, or its state changed before this transaction.
    The caller must not invoke the model or write artifacts in that case.

    Any database outage or malformed database row escapes the context and rolls
    back. The caller will later give that retryable/fatal category a reviewed
    policy; this boundary never converts it to a successful running state.
    """

    if not isinstance(preflight, DemucsAcknowledgedLeasePreflight):
        raise TypeError("preflight must be DemucsAcknowledgedLeasePreflight.")

    # This `with` ends before a future caller can start a CPU/GPU process. The
    # transaction therefore never remains open through media work, and a normal
    # return cannot expose model-eligible evidence until PostgreSQL has
    # committed the lease-token guard.
    with database.write_cursor() as cursor:
        started_at = start_leased_demucs_task(cursor, lease=preflight.lease)

    if started_at is None:
        return None
    return DemucsRunningSource(
        lease=preflight.lease,
        source=preflight.source,
        started_at=started_at,
    )
