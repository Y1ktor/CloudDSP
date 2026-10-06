"""Start and renew work only while PostgreSQL recognizes its current lease.

An unexpired token is required by both statements. A no-row result revokes
permission to continue; the caller owns transaction commit and must stop
external work after ownership loss. Source preflight precedes the separate
leased-to-running transition, so rejected media never starts model execution.
"""

from __future__ import annotations

from datetime import datetime

from .contracts import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    DatabaseCursor,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
)
from .validation import _canonical_uuid, _mapping_or_error, _validated_lease_seconds


# A worker must renew only the lease token it still owns and only before expiry.
# Once another replica has recovered a task, this guarded update returns no row
# and the stale worker must stop without writing stems or database state.
RENEW_DEMUCS_TASK_LEASE_SQL = """
    UPDATE public.processing_tasks
    SET lease_expires_at = CURRENT_TIMESTAMP + (%s::integer * INTERVAL '1 second')
    WHERE task_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND status IN ('leased', 'running')
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING lease_expires_at
"""


# Source validation happens while the task remains `leased`: a permanent media
# rejection can therefore finish without pretending Demucs model execution ever
# began. Only after that preflight succeeds may the owner atomically move into
# `running`. The token, current active state, PostgreSQL clock, and immutable
# job/stage identity all remain in the predicate so a stale worker sees no row
# after expiry or recovery instead of overwriting another replica's task.
# `COALESCE` keeps the first genuine start timestamp across a later recovery:
# recovery moves an expired `running` task back to `leased`, but it must not
# erase the fact that an earlier attempt had started the model stage.
START_LEASED_DEMUCS_TASK_SQL = """
    UPDATE public.processing_tasks
    SET
      status = 'running',
      started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
    WHERE task_id = %s::uuid
      AND job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND status = 'leased'
      AND lease_token = %s::uuid
      AND lease_expires_at > CURRENT_TIMESTAMP
    RETURNING started_at
"""


def renew_demucs_task_lease(
    cursor: DatabaseCursor,
    *,
    task_id: str,
    lease_token: str,
    lease_seconds: int = DEFAULT_DEMUCS_LEASE_SECONDS,
) -> datetime | None:
    """Extend only the still-current active lease and return its new expiry.

    ``None`` means the lease expired, a recovery worker replaced its token, or
    the task became inactive. The caller must stop processing immediately; it
    must never keep writing results under an ownership token PostgreSQL no
    longer recognizes.
    """

    bounded_lease_seconds = _validated_lease_seconds(lease_seconds)
    canonical_task_id = _canonical_uuid(task_id)
    canonical_lease_token = _canonical_uuid(lease_token)
    cursor.execute(
        RENEW_DEMUCS_TASK_LEASE_SQL,
        (bounded_lease_seconds, canonical_task_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    returned = _mapping_or_error(row).get("lease_expires_at")
    if not isinstance(returned, datetime) or returned.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return returned


def start_leased_demucs_task(
    cursor: DatabaseCursor,
    *,
    lease: DemucsTaskLease,
) -> datetime | None:
    """Atomically mark one preflight-validated lease as running, or stop safely.

    The caller must invoke this only after its acknowledged-lease source
    preflight has returned valid evidence, and inside one short write
    transaction. A timestamp means PostgreSQL still recognizes this exact
    unexpired token and the future runtime may *then* begin the Demucs model.
    ``None`` is a normal ownership-loss signal: the task may have expired,
    been recovered by another replica, or become inactive. The caller must not
    start the model, write artifacts, or issue a result transition in that case.

    This pure adapter does not read MinIO, inspect preflight evidence, create a
    connection, commit, renew a lease, invoke a model, or retry. Separating the
    source handoff from this short SQL decision keeps no database lock alive
    while media bytes are downloaded or inspected.
    """

    if not isinstance(lease, DemucsTaskLease):
        raise TypeError("lease must be DemucsTaskLease.")
    canonical_task_id = _canonical_uuid(lease.task_id)
    canonical_job_id = _canonical_uuid(lease.job_id)
    canonical_lease_token = _canonical_uuid(lease.lease_token)
    cursor.execute(
        START_LEASED_DEMUCS_TASK_SQL,
        (canonical_task_id, canonical_job_id, canonical_lease_token),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    started_at = _mapping_or_error(row).get("started_at")
    if not isinstance(started_at, datetime) or started_at.tzinfo is None:
        raise DemucsTaskLeaseProtocolError("Demucs task database state is invalid.")
    return started_at
