"""Commit one verified Basic Pitch MIDI task completion in a short transaction.

This composition contains no model or object-storage work. The prior layers
already produced ``VerifiedStoredBasicPitchMidiObject`` evidence, and
``midi_task_completion.py`` owns the one lease-token-guarded SQL statement.
This module supplies only the commit-or-rollback boundary: a non-``None``
completion becomes visible to the caller only after the restricted PostgreSQL
transaction exits normally.

It does not create a connection, read/write MinIO, receive/acknowledge
RabbitMQ, renew a lease, classify failures, invoke Basic Pitch, update the
overall Job, or interact with Kubernetes.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.midi_artifact_head_object import VerifiedStoredBasicPitchMidiObject
from app.midi_task_completion import (
    BasicPitchMidiTaskCompletion,
    complete_running_basic_pitch_task,
)
from app.task_lease import BasicPitchTaskLease, DatabaseCursor
from app.tempo_candidate import BasicPitchTempoCandidate


class BasicPitchMidiTaskCompletionDatabase(Protocol):
    """The one database capability needed for the final per-stem state write."""

    def write_cursor(self) -> AbstractContextManager[DatabaseCursor]:
        """Yield a short cursor that commits on normal exit and rolls back on error."""


def commit_verified_basic_pitch_midi_task(
    *,
    database: BasicPitchMidiTaskCompletionDatabase,
    lease: BasicPitchTaskLease,
    stored_midi: VerifiedStoredBasicPitchMidiObject,
    tempo_candidate: BasicPitchTempoCandidate,
) -> BasicPitchMidiTaskCompletion | None:
    """Commit a guarded Basic Pitch task success, or return normal ownership loss.

    The stored MIDI evidence and lease are passed straight to the pure SQL
    adapter *inside* the write context. If it returns completion evidence, the
    context exits and commits before this function returns it. ``None`` is a
    normal no-row stale-owner result and also exits normally—there was no
    mutation to roll back. Any adapter/database exception leaves the context
    exceptionally, so its transaction rolls back before the caller can decide
    retry/failure or RabbitMQ acknowledgement.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    with database.write_cursor() as cursor:
        completion = complete_running_basic_pitch_task(
            cursor,
            lease=lease,
            stored_midi=stored_midi,
            tempo_candidate=tempo_candidate,
        )
    return completion
