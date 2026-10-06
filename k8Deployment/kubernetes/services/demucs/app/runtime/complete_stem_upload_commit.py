"""Commit a fully uploaded Demucs stem set through the existing guarded SQL path.

This is the narrow bridge between two completed responsibilities:

1. ``complete_stem_upload.py`` returns a receipt set only after every fixed
   private MinIO upload succeeds; and
2. ``demucs_task_completion.py`` performs the existing short, lease-token-
   guarded PostgreSQL task/Job/outbox transaction.

The function validates the database transaction shape *before* starting any
upload, then performs external MinIO work with no open transaction, and finally
calls the one guarded completion transaction. It never publishes RabbitMQ
events itself: downstream requests become durable outbox rows only if the
transaction commits, and the separate dispatcher owns broker publication.
"""

from __future__ import annotations

from typing import Callable
from uuid import UUID, uuid4

from app.artifacts.complete_stem_upload import upload_complete_demucs_stem_set_from_plan_workspace
from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.db.demucs_task_completion import (
    CommittedDemucsStemSet,
    DemucsTaskCompletionDatabase,
    commit_published_demucs_stem_set,
)
from app.artifacts.planned_stem_upload import DemucsPlannedStemUploader
from app.artifacts.stem_output_plan_workspace import DemucsStemOutputPlanWorkspace


def _plan_workspace_or_raise(value: object) -> DemucsStemOutputPlanWorkspace:
    """Require the full validated/hash/plan chain before external work starts."""

    if not isinstance(value, DemucsStemOutputPlanWorkspace):
        raise TypeError("workspace must be DemucsStemOutputPlanWorkspace.")
    return value


def _database_or_raise(value: object) -> DemucsTaskCompletionDatabase:
    """Fail before MinIO writes when the required guarded transaction is absent."""

    if not callable(getattr(value, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    # Structural protocols intentionally are not runtime-checkable. The later
    # completion composition performs the same capability check before it opens
    # its short transaction, while this early check prevents uploads without a
    # configured durable completion path.
    return value  # type: ignore[return-value]


def _event_id_factory_or_raise(value: object) -> Callable[[], UUID]:
    """Require a future outbox-ID source before any private object write starts."""

    if not callable(value):
        raise TypeError("event_id_factory must be callable.")
    # Do not call it here: the existing guarded transaction must create IDs only
    # after it has validated complete receipt evidence and is ready to insert the
    # corresponding durable outbox rows.
    return value  # type: ignore[return-value]


def upload_and_commit_demucs_stem_set(
    *,
    workspace: DemucsStemOutputPlanWorkspace,
    client: DemucsPutObjectClient,
    database: DemucsTaskCompletionDatabase,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> CommittedDemucsStemSet | None:
    """Upload every fixed stem, then commit only the complete receipt set.

    ``None`` is the existing committed ownership-loss signal: all private
    objects may exist, but PostgreSQL found that the exact ``running`` lease no
    longer owns a completable task, so no durable success or downstream work is
    exposed. Storage or database failures propagate to a future retry policy;
    this function does not catch, classify, retry, clean objects, renew a lease,
    or send an AMQP acknowledgement.
    """

    plan_workspace = _plan_workspace_or_raise(workspace)
    completion_database = _database_or_raise(database)
    completion_event_id_factory = _event_id_factory_or_raise(event_id_factory)
    # This call can perform several bounded MinIO writes. It ends completely
    # before the next call opens PostgreSQL locks, preserving the durable design
    # rule that no transaction remains open during network/file I/O.
    published = upload_complete_demucs_stem_set_from_plan_workspace(
        workspace=plan_workspace,
        client=client,
        uploader=uploader,
    )
    return commit_published_demucs_stem_set(
        database=completion_database,
        published=published,
        event_id_factory=completion_event_id_factory,
    )
