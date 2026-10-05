"""Shared immutable lease evidence, safe errors, and finite Demucs task vocabulary.

All adapters and the public ``app.db.task_lease`` facade import these same
classes. Keeping a single definition matters because orchestration validates
lease/result identity with ``isinstance`` before starting external work.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


# A normal local CPU or future GPU worker starts with a 15-minute lease and
# renews it at least once per minute. Bounds make a later Deployment typo unable
# to make a task effectively permanent or create an impractically short lease.
DEFAULT_DEMUCS_LEASE_SECONDS = 15 * 60


MIN_DEMUCS_LEASE_SECONDS = 60


MAX_DEMUCS_LEASE_SECONDS = 60 * 60


MAX_DEMUCS_TASK_ATTEMPTS = 3


MAX_DEMUCS_STEMS_DOCUMENT_BYTES = 16 * 1024


# Six is the largest reviewed Demucs output set.  Keeping this limit alongside
# the JSON-byte cap prevents an accidental future caller from turning one task
# completion into an unbounded downstream fan-out transaction.
MAX_DEMUCS_DOWNSTREAM_OUTBOX_EVENTS = 6


MAX_DEMUCS_DOWNSTREAM_OUTBOX_DOCUMENT_BYTES = 16 * 1024


# A Pod can disappear after starting a third attempt, leaving no Python
# exception to classify. PostgreSQL's recovery scan records this distinct,
# bounded fact instead of pretending that a model/storage failure occurred or
# issuing a prohibited fourth lease. The same safe code is written to the
# terminal Demucs task and its still-processable authoritative Job.
DEMUCS_EXHAUSTED_LEASE_ERROR_CODE = "demucs_lease_expired_attempts_exhausted"


# These mappings are the durable routing decision made when Demucs has
# completed—not a RabbitMQ route or a worker implementation.  Drums go only
# to ADTOF, while every non-drum output can be pitch-analysed by Basic Pitch.
# The immutable v004 migration mirrors this exact finite vocabulary in its
# database CHECK constraint, so application and storage reject a widened stage
# independently.
DEMUCS_DOWNSTREAM_STAGE_BY_STEM = {
    "vocals": "basic-pitch",
    "no_vocals": "basic-pitch",
    "drums": "adtof",
    "bass": "basic-pitch",
    "other": "basic-pitch",
    "guitar": "basic-pitch",
    "piano": "basic-pitch",
}


DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE = {
    "basic-pitch": "basic-pitch.requested",
    "adtof": "adtof.requested",
}


class DemucsTaskLeaseProtocolError(RuntimeError):
    """Raise a safe category for unexpected database row shapes or settings.

    No raw database diagnostic, object key, user identity, or message body is
    included in this exception. A later supervisor can retain the driver cause
    privately while logging only this fixed category.
    """


class DemucsTaskClaimInconsistency(RuntimeError):
    """A parsed delivery disagrees with authoritative durable state.

    This is intentionally distinct from a stale request or harmless duplicate.
    The later result-transition adapter must record a bounded terminal category
    before acknowledging it; this narrow claim module never guesses a failure
    update or sends an AMQP acknowledgement by itself.
    """


class DemucsTaskClaimDisposition(StrEnum):
    """The only safe outcomes of a message-driven first-claim transaction."""

    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    STALE = "stale"


class DemucsStaleRequestReason(StrEnum):
    """Non-sensitive reasons a valid historic delivery is safe to acknowledge."""

    JOB_MISSING = "job_missing"
    JOB_EXPIRED = "job_expired"
    JOB_TERMINAL = "job_terminal"


class DatabaseCursor(Protocol):
    """The minimal dictionary-row cursor API used by this testable SQL boundary."""

    def execute(self, query: str, params: tuple[object, ...]) -> object:
        """Execute parameterized SQL; data never interpolates into query text."""

    def fetchone(self) -> Mapping[str, object] | None:
        """Return at most one dictionary-shaped PostgreSQL row."""


@dataclass(frozen=True)
class DemucsTaskLease:
    """One durable worker lease returned after a first/recovery claim commits."""

    task_id: str
    job_id: str
    request_event_id: str
    input_bucket: str
    input_object_key: str
    stem_mode: str
    attempt_count: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True)
class DemucsTaskCompletion:
    """One committed Demucs success transition returned after its SQL statement.

    ``job_revision`` is the revision written with the complete stem map, and
    ``outbox_event_count`` proves the same SQL statement inserted every
    downstream request.  The outer composition exposes either value only
    after the surrounding short transaction commits, never while Job locks
    remain open.
    """

    task_id: str
    job_id: str
    completed_at: datetime
    job_revision: int
    outbox_event_count: int


@dataclass(frozen=True)
class DemucsExpiredLeaseTerminalization:
    """Evidence that PostgreSQL terminalized an overdue or exhausted task.

    The owner either exceeded the overall model deadline or the final lease
    expired. This result
    proves the task and its Job moved together to their terminal states in one
    committed statement. It excludes model output, source/object coordinates,
    lease tokens, credentials, and raw driver diagnostics.
    """

    task_id: str
    job_id: str
    attempt_count: int
    completed_at: datetime
    job_revision: int
    error_code: str


@dataclass(frozen=True)
class DemucsTaskClaimResult:
    """A claim result that tells the later transport whether an ack is allowed.

    Only ``CLAIMED`` has a lease. ``DUPLICATE`` means another durable task record
    already owns or completed this logical stage and is safe to acknowledge.
    ``STALE`` means a deleted, expired, or terminal Job needs no new state. An
    inconsistency raises instead of yielding a deceptively safe result.
    """

    disposition: DemucsTaskClaimDisposition
    lease: DemucsTaskLease | None = None
    duplicate_status: str | None = None
    stale_reason: DemucsStaleRequestReason | None = None
