"""Compose one MinIO event parser, HeadObject verifier, and SQL transition.

This module is the first application-level upload-intake flow. It remains
transport-neutral: it does not import an AMQP client, acknowledge a RabbitMQ
delivery, open a PostgreSQL connection, or start a container. A later runtime
adapter will give it a real transaction provider and, only after this function
returns successfully, manually acknowledge the AMQP message.

Keeping acknowledgement outside this function makes the durability rule clear:
every actionable record must be committed to PostgreSQL before RabbitMQ can be
told that the notification is complete. A process crash after a commit but
before acknowledgement produces a normal duplicate delivery; the conditional
revision update below turns that duplicate into a harmless no-op.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.database_transition import (
    DatabaseCursor,
    SourceUploadStateChange,
    SourceUploadTransitionOutcome,
    find_pending_direct_upload,
    mark_permanently_invalid_source,
    mark_verified_source_uploaded_and_enqueue_demucs,
)
from app.minio_event import (
    IgnoredMinioEventRecord,
    MinioEventEnvelopeError,
    SourceUploadCandidate,
    parse_direct_upload_event,
)
from app.object_storage import (
    HeadObjectClient,
    ObjectStorageSettings,
    PermanentSourceVerificationError,
    verify_pending_direct_upload_head_object,
)


class SourceIntakeDatabase(Protocol):
    """Transaction contexts supplied by the concrete Psycopg adapter.

    ``read_cursor`` opens a short read-only database scope and closes it before
    the potentially slower MinIO request. ``write_cursor`` begins one write
    transaction and commits only if the context exits normally. Splitting the
    scopes avoids holding a PostgreSQL transaction open during network I/O,
    while the conditional revision UPDATE preserves correctness across the
    resulting race window.
    """

    def read_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one cursor for a read-only pending-job lookup."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield one cursor whose successful exit commits the full write unit."""


class SourceIntakeEnvelopeOutcome(StrEnum):
    """The safe high-level result of parsing one AMQP message body."""

    # Every candidate either reached a durable outcome or was non-actionable.
    HANDLED = "handled"
    # Invalid JSON/envelope shape can never become valid through retry, so a
    # later AMQP adapter acknowledges it after recording only this category.
    INVALID_ENVELOPE = "invalid_envelope"


class SourceIntakeCandidateOutcome(StrEnum):
    """The non-sensitive durable/no-op result of one accepted event record."""

    SOURCE_UPLOADED = "source_uploaded"
    FAILED = "failed"
    NO_LONGER_PENDING = "no_longer_pending"


@dataclass(frozen=True)
class SourceIntakeCandidateResult:
    """One record outcome safe for metrics without logging keys or user IDs."""

    record_index: int
    outcome: SourceIntakeCandidateOutcome


@dataclass(frozen=True)
class SourceIntakeMessageResult:
    """A completed message is safe for the later AMQP adapter to acknowledge.

    This result deliberately contains only record indexes and fixed categories.
    It contains no raw AMQP body, object key, bucket, job UUID, S3 metadata,
    database exception, access token, or credential that a future log call
    could expose accidentally.
    """

    envelope_outcome: SourceIntakeEnvelopeOutcome
    candidate_results: tuple[SourceIntakeCandidateResult, ...]
    ignored_records: tuple[IgnoredMinioEventRecord, ...]

    @property
    def safe_to_acknowledge(self) -> bool:
        """Return true only because this function returns after durable work.

        Transient object-storage/database failures are intentionally exceptions,
        not a result with a false value. That makes it harder for a future AMQP
        adapter to accidentally acknowledge an outage or an incomplete write.
        """

        return True


def _candidate_result(
    *,
    candidate: SourceUploadCandidate,
    state_change: SourceUploadStateChange,
) -> SourceIntakeCandidateResult:
    """Map the narrow SQL outcome to an equally narrow no-log result object."""

    outcome_by_state_change = {
        SourceUploadTransitionOutcome.SOURCE_UPLOADED: SourceIntakeCandidateOutcome.SOURCE_UPLOADED,
        SourceUploadTransitionOutcome.FAILED: SourceIntakeCandidateOutcome.FAILED,
        SourceUploadTransitionOutcome.NO_LONGER_PENDING: SourceIntakeCandidateOutcome.NO_LONGER_PENDING,
    }
    return SourceIntakeCandidateResult(
        record_index=candidate.record_index,
        outcome=outcome_by_state_change[state_change.outcome],
    )


def _process_candidate(
    *,
    candidate: SourceUploadCandidate,
    database: SourceIntakeDatabase,
    object_client: HeadObjectClient,
    object_storage_settings: ObjectStorageSettings,
) -> SourceIntakeCandidateResult:
    """Give one syntactically valid record one durable outcome or raise.

    The MinIO HEAD occurs after the narrow read scope has closed. The write uses
    the selected row's revision as an optimistic lock, so another Pod can win
    while HEAD is in flight without permitting this worker to overwrite the
    newer state. There is no queue acknowledgement here.
    """

    with database.read_cursor() as read_cursor:
        pending = find_pending_direct_upload(read_cursor, candidate=candidate)

    if pending is None:
        # A missing, expired, deleted, already-uploaded, failed, or later-stage
        # job is permanently non-actionable. Do not touch MinIO or PostgreSQL
        # again just to distinguish those intentionally hidden cases.
        return SourceIntakeCandidateResult(
            record_index=candidate.record_index,
            outcome=SourceIntakeCandidateOutcome.NO_LONGER_PENDING,
        )

    try:
        verify_pending_direct_upload_head_object(
            object_client,
            pending=pending,
            settings=object_storage_settings,
        )
    except PermanentSourceVerificationError as error:
        # The verifier exposes only a fixed category. Persist it atomically
        # under the same pending/key/revision conditions; an update race becomes
        # a safe no-op rather than marking a newer job state failed.
        with database.write_cursor() as write_cursor:
            state_change = mark_permanently_invalid_source(
                write_cursor,
                candidate=candidate,
                expected_revision=pending.revision,
                category=error.category,
            )
    else:
        # A successful HeadObject comparison authorizes the initial source
        # state transition *and* creates its durable Demucs outbox event in one
        # PostgreSQL transaction. It still does not publish to RabbitMQ: the
        # later dispatcher owns broker confirmation, retry, and lease recovery.
        with database.write_cursor() as write_cursor:
            state_change = mark_verified_source_uploaded_and_enqueue_demucs(
                write_cursor,
                candidate=candidate,
                pending=pending,
                expected_revision=pending.revision,
            )

    return _candidate_result(candidate=candidate, state_change=state_change)


def handle_source_intake_message(
    body: str | bytes | bytearray,
    *,
    database: SourceIntakeDatabase,
    object_client: HeadObjectClient,
    object_storage_settings: ObjectStorageSettings,
) -> SourceIntakeMessageResult:
    """Handle every record in one MinIO AMQP body without queue side effects.

    A malformed envelope is a permanent protocol fault and returns an explicit
    acknowledgement-safe category. Valid sibling records run independently.
    If a later record hits transient MinIO/PostgreSQL trouble, its exception
    propagates: earlier committed transitions can be redelivered safely because
    their revision-guarded SQL now returns ``no_longer_pending``. The future
    AMQP adapter must acknowledge only after this function returns normally.
    """

    try:
        parsed_event = parse_direct_upload_event(body)
    except MinioEventEnvelopeError:
        return SourceIntakeMessageResult(
            envelope_outcome=SourceIntakeEnvelopeOutcome.INVALID_ENVELOPE,
            candidate_results=(),
            ignored_records=(),
        )

    candidate_results = tuple(
        _process_candidate(
            candidate=candidate,
            database=database,
            object_client=object_client,
            object_storage_settings=object_storage_settings,
        )
        for candidate in parsed_event.candidates
    )
    return SourceIntakeMessageResult(
        envelope_outcome=SourceIntakeEnvelopeOutcome.HANDLED,
        candidate_results=candidate_results,
        ignored_records=parsed_event.ignored_records,
    )
