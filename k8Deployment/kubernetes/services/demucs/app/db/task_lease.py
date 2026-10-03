"""Public PostgreSQL task-lease interface for the local Demucs worker.

The implementation is grouped by responsibility in ``app.db.task_leases``:
contracts, row validation, delivery claims, start/renewal, recovery, and
completion. Re-exports preserve existing worker imports and one canonical
class identity; no wrapper opens a connection or changes transaction ordering.

PostgreSQL remains authoritative for the ``(job_id, 'demucs', '')`` task.
Callers own short transactions and commit a first claim before AMQP ACK.
Start follows source preflight; renewal and completion require current leases.
The adapters perform no RabbitMQ, MinIO, model, or Kubernetes operations.
"""

# These domain constants/types were available through the original module.
# Keep that import surface while their defining modules retain ownership.
from app.artifacts.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE
from app.messaging.demucs_requested_message import DemucsRequestedMessage
from app.processing.demucs_process import (
    DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    DEMUCS_TIMEOUT_ERROR_CODE,
)

from .task_leases.contracts import (
    DEFAULT_DEMUCS_LEASE_SECONDS,
    MIN_DEMUCS_LEASE_SECONDS,
    MAX_DEMUCS_LEASE_SECONDS,
    MAX_DEMUCS_TASK_ATTEMPTS,
    MAX_DEMUCS_STEMS_DOCUMENT_BYTES,
    MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS,
    MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES,
    DEMUCS_EXHAUSTED_LEASE_ERROR_CODE,
    DEMUCS_DOWNSTREAM_STAGE_BY_STEM,
    DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE,
    DemucsTaskLeaseProtocolError,
    DemucsTaskClaimInconsistency,
    DemucsTaskClaimDisposition,
    DemucsStaleRequestReason,
    DatabaseCursor,
    DemucsTaskLease,
    DemucsTaskCompletion,
    DemucsExpiredLeaseTerminalization,
    DemucsTaskClaimResult,
)

from .task_leases.claim import (
    LOCK_EXISTING_DEMUCS_TASK_SQL,
    LOCK_JOB_FOR_DEMUCS_CLAIM_SQL,
    READ_PUBLISHED_DEMUCS_OUTBOX_SQL,
    INSERT_FIRST_DEMUCS_TASK_LEASE_SQL,
    claim_demucs_task_for_delivery,
)

from .task_leases.ownership import (
    RENEW_DEMUCS_TASK_LEASE_SQL,
    START_LEASED_DEMUCS_TASK_SQL,
    renew_demucs_task_lease,
    start_leased_demucs_task,
)

from .task_leases.recovery import (
    CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL,
    FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL,
    FINALIZE_NEXT_OVERDUE_DEMUCS_TASK_SQL,
    claim_next_recoverable_demucs_task,
    finalize_next_expired_exhausted_demucs_task,
    finalize_next_overdue_demucs_task,
)

from .task_leases.completion import (
    COMPLETE_RUNNING_DEMUCS_TASK_SQL,
    complete_running_demucs_task,
)


__all__ = [
    "DEMUCS_STEM_FILE_EXTENSION",
    "DEMUCS_STEMS_BY_MODE",
    "DemucsRequestedMessage",
    "DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS",
    "DEMUCS_TIMEOUT_ERROR_CODE",
    "DEFAULT_DEMUCS_LEASE_SECONDS",
    "MIN_DEMUCS_LEASE_SECONDS",
    "MAX_DEMUCS_LEASE_SECONDS",
    "MAX_DEMUCS_TASK_ATTEMPTS",
    "MAX_DEMUCS_STEMS_DOCUMENT_BYTES",
    "MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS",
    "MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES",
    "DEMUCS_EXHAUSTED_LEASE_ERROR_CODE",
    "DEMUCS_DOWNSTREAM_STAGE_BY_STEM",
    "DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE",
    "DemucsTaskLeaseProtocolError",
    "DemucsTaskClaimInconsistency",
    "DemucsTaskClaimDisposition",
    "DemucsStaleRequestReason",
    "DatabaseCursor",
    "DemucsTaskLease",
    "DemucsTaskCompletion",
    "DemucsExpiredLeaseTerminalization",
    "DemucsTaskClaimResult",
    "LOCK_EXISTING_DEMUCS_TASK_SQL",
    "LOCK_JOB_FOR_DEMUCS_CLAIM_SQL",
    "READ_PUBLISHED_DEMUCS_OUTBOX_SQL",
    "INSERT_FIRST_DEMUCS_TASK_LEASE_SQL",
    "claim_demucs_task_for_delivery",
    "RENEW_DEMUCS_TASK_LEASE_SQL",
    "START_LEASED_DEMUCS_TASK_SQL",
    "renew_demucs_task_lease",
    "start_leased_demucs_task",
    "CLAIM_NEXT_RECOVERABLE_DEMUCS_TASK_SQL",
    "FINALIZE_NEXT_EXPIRED_EXHAUSTED_DEMUCS_TASK_SQL",
    "FINALIZE_NEXT_OVERDUE_DEMUCS_TASK_SQL",
    "claim_next_recoverable_demucs_task",
    "finalize_next_expired_exhausted_demucs_task",
    "finalize_next_overdue_demucs_task",
    "COMPLETE_RUNNING_DEMUCS_TASK_SQL",
    "complete_running_demucs_task",
]
