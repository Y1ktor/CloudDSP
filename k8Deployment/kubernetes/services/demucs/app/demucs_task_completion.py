"""Commit one complete uploaded Demucs stem set through PostgreSQL safely.

The preceding publication composition proves every expected WAV was uploaded
to its deterministic private MinIO key.  This module turns that in-memory
evidence into the one compact ``jobs.stems`` JSON document plus one narrow
Basic Pitch or ADTOF request per actual stem.  The pure guarded completion SQL
updates the task/Job and inserts all downstream outbox records in one short
PostgreSQL transaction.  It returns durable completion only after that
transaction commits.

This module still does not publish or acknowledge RabbitMQ, touch MinIO,
renew a lease, classify/retry errors, execute Demucs, build an image, or change
Kubernetes.  A later dispatcher will publish these already-durable requests.
"""

from __future__ import annotations

import json
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.demucs_artifacts import DEMUCS_STEM_FILE_EXTENSION, DEMUCS_STEMS_BY_MODE
from app.demucs_artifact_upload import UploadedDemucsStemObject
from app.demucs_output_object import (
    DEMUCS_ARTIFACT_CONTENT_TYPE,
    DEMUCS_ARTIFACT_KEY_PREFIX,
    LOCAL_DEMUCS_ARTIFACT_BUCKET,
)
from app.demucs_stem_set_publish import PublishedDemucsStemSet
from app.task_lease import (
    DatabaseCursor,
    DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE,
    DEMUCS_DOWNSTREAM_STAGE_BY_STEM,
    DemucsTaskCompletion,
    DemucsTaskLease,
    complete_running_demucs_task,
)


class DemucsTaskCompletionContractError(RuntimeError):
    """A published stem set cannot be safely represented as durable Job state."""


class DemucsTaskCompletionDatabase(Protocol):
    """The one short write transaction capability required by this boundary."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a cursor whose normal exit commits and exception exit rolls back."""


@dataclass(frozen=True)
class CompletedDemucsStem:
    """One verified stable artifact retained in the Job's durable stem map."""

    stem_name: str
    bucket: str
    object_key: str
    content_length: int
    sha256: str


@dataclass(frozen=True)
class DemucsDownstreamOutboxEvent:
    """One durable post-Demucs work request, before any broker publication.

    The event keeps its routing key separately from its body.  A future
    dispatcher can convert it to the reviewed worker-specific AMQP contract
    without re-reading a mutable ``jobs.stems`` map, while PostgreSQL's unique
    ``(job_id, stage, stem_name)`` key makes the request idempotent.
    """

    event_id: str
    stage: str
    stem_name: str
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True)
class CommittedDemucsStemSet:
    """Completion evidence available only after the task/Job transaction commits."""

    lease: DemucsTaskLease
    stems: tuple[CompletedDemucsStem, ...]
    completed_at: datetime
    job_revision: int
    downstream_events: tuple[DemucsDownstreamOutboxEvent, ...]


def _contract_error() -> DemucsTaskCompletionContractError:
    """Return one non-sensitive invalid-publication category."""

    return DemucsTaskCompletionContractError("Demucs task completion contract is invalid.")


def _canonical_uuid(value: object) -> str:
    """Require canonical UUID text before it determines a private object key."""

    if not isinstance(value, str):
        raise _contract_error()
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _contract_error() from error
    if value != canonical:
        raise _contract_error()
    return canonical


def _validated_complete_stems(value: object) -> tuple[DemucsTaskLease, tuple[CompletedDemucsStem, ...]]:
    """Require all and only the current task mode's stable uploaded receipts."""

    if not isinstance(value, PublishedDemucsStemSet):
        raise _contract_error()
    lease = value.lease
    if not isinstance(lease, DemucsTaskLease):
        raise _contract_error()
    job_id = _canonical_uuid(lease.job_id)
    _canonical_uuid(lease.task_id)
    _canonical_uuid(lease.request_event_id)
    _canonical_uuid(lease.lease_token)
    if (
        lease.input_bucket != LOCAL_DEMUCS_ARTIFACT_BUCKET
        or not isinstance(lease.input_object_key, str)
        or not lease.input_object_key.startswith(f"uploads/{job_id}/")
        or not lease.input_object_key.removeprefix(f"uploads/{job_id}/")
        or "/" in lease.input_object_key.removeprefix(f"uploads/{job_id}/")
        or not isinstance(lease.stem_mode, str)
        or lease.stem_mode not in DEMUCS_STEMS_BY_MODE
        or not isinstance(value.uploads, tuple)
    ):
        raise _contract_error()
    expected_stems = DEMUCS_STEMS_BY_MODE[lease.stem_mode][1]
    if len(value.uploads) != len(expected_stems):
        raise _contract_error()

    stems: list[CompletedDemucsStem] = []
    for stem_name, receipt in zip(expected_stems, value.uploads, strict=True):
        if (
            not isinstance(receipt, UploadedDemucsStemObject)
            or receipt.bucket != LOCAL_DEMUCS_ARTIFACT_BUCKET
            or receipt.object_key
            != f"{DEMUCS_ARTIFACT_KEY_PREFIX}/{job_id}/{stem_name}{DEMUCS_STEM_FILE_EXTENSION}"
            or type(receipt.content_length) is not int
            or receipt.content_length < 1
            or not isinstance(receipt.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt.sha256)
        ):
            raise _contract_error()
        stems.append(
            CompletedDemucsStem(
                stem_name=stem_name,
                bucket=receipt.bucket,
                object_key=receipt.object_key,
                content_length=receipt.content_length,
                sha256=receipt.sha256,
            )
        )
    return lease, tuple(stems)


def _stems_document(stems: tuple[CompletedDemucsStem, ...]) -> str:
    """Build a stable cloud-compatible ``jobs.stems`` JSONB object parameter.

    Existing CloudDSP consumers use ``status`` and ``s3_key``.  The local
    record retains those fields unchanged while adding bucket, fixed WAV type,
    byte length, and SHA-256 so a later independent Basic Pitch/ADTOF worker
    can verify the exact object it was asked to process.
    """

    document = {
        stem.stem_name: {
            "status": "ready",
            "s3_key": stem.object_key,
            "bucket": stem.bucket,
            "content_type": DEMUCS_ARTIFACT_CONTENT_TYPE,
            "size_bytes": stem.content_length,
            "sha256": stem.sha256,
        }
        for stem in stems
    }
    # Compact sorted JSON makes each pure SQL test and future transaction log
    # deterministic.  It remains a query parameter, never SQL text.
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _new_event_id(factory: Callable[[], UUID]) -> str:
    """Generate a canonical application UUID for one immutable outbox record."""

    value = factory()
    if not isinstance(value, UUID):
        raise _contract_error()
    return str(value)


def _downstream_events(
    *,
    lease: DemucsTaskLease,
    stems: tuple[CompletedDemucsStem, ...],
    event_id_factory: Callable[[], UUID],
) -> tuple[DemucsDownstreamOutboxEvent, ...]:
    """Route each verified stem to its one next-stage durable request.

    The payload contains only immutable private-object evidence required by a
    later worker: no presigned URL, S3 credential, browser identity, task
    lease token, exception text, or temporary path can cross this boundary.
    ``_validated_complete_stems`` has already proved both the exact stem set
    and its deterministic object coordinates before this helper is called.
    """

    job_id = _canonical_uuid(lease.job_id)
    events: list[DemucsDownstreamOutboxEvent] = []
    for stem in stems:
        stage = DEMUCS_DOWNSTREAM_STAGE_BY_STEM.get(stem.stem_name)
        if stage is None:
            raise _contract_error()
        event_type = DEMUCS_DOWNSTREAM_EVENT_TYPE_BY_STAGE.get(stage)
        if event_type is None:
            raise _contract_error()
        events.append(
            DemucsDownstreamOutboxEvent(
                event_id=_new_event_id(event_id_factory),
                stage=stage,
                stem_name=stem.stem_name,
                event_type=event_type,
                payload={
                    "schema_version": 1,
                    "job_id": job_id,
                    "stem_name": stem.stem_name,
                    "stem": {
                        "bucket": stem.bucket,
                        "object_key": stem.object_key,
                        "content_type": DEMUCS_ARTIFACT_CONTENT_TYPE,
                        "size_bytes": stem.content_length,
                        "sha256": stem.sha256,
                    },
                },
            )
        )
    return tuple(events)


def _downstream_events_document(events: tuple[DemucsDownstreamOutboxEvent, ...]) -> str:
    """Serialize only the five columns accepted by the completion SQL CTE."""

    if not events:
        raise _contract_error()
    document = [
        {
            "event_id": event.event_id,
            "stage": event.stage,
            "stem_name": event.stem_name,
            "event_type": event.event_type,
            "payload": event.payload,
        }
        for event in events
    ]
    # Compact sorted JSON gives the adjacent pure SQL adapter a repeatable
    # parameter. It is still never concatenated into a SQL statement.
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def commit_published_demucs_stem_set(
    *,
    database: DemucsTaskCompletionDatabase,
    published: PublishedDemucsStemSet,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> CommittedDemucsStemSet | None:
    """Persist one complete stem set only if the exact running lease is current.

    A ``None`` result is a normal committed ownership-loss stop signal: a
    recovery/deletion/expiry/status change won the race after the private
    objects uploaded.  No caller may publish downstream work from that result.
    Any database/driver/row-shape exception propagates through the context so
    its transaction rolls back before the future supervisor chooses retry.
    """

    # Protocols are structural and deliberately not runtime-checkable. Keep the
    # message useful without binding this source module to Psycopg.
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    lease, stems = _validated_complete_stems(published)
    stems_document = _stems_document(stems)
    downstream_events = _downstream_events(
        lease=lease,
        stems=stems,
        event_id_factory=event_id_factory,
    )
    downstream_events_document = _downstream_events_document(downstream_events)

    # This scope is intentionally after MinIO/network/model work.  It holds
    # the Job lock only for the guarded state write and returns a result only
    # after the context manager has committed.
    with database.write_cursor() as cursor:
        completion = complete_running_demucs_task(
            cursor,
            lease=lease,
            stems_document=stems_document,
            downstream_events_document=downstream_events_document,
        )

    if completion is None:
        return None
    return CommittedDemucsStemSet(
        lease=lease,
        stems=stems,
        completed_at=completion.completed_at,
        job_revision=completion.job_revision,
        downstream_events=downstream_events,
    )
