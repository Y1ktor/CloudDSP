"""Promote one acknowledged source workspace to a model-eligible workspace.

The preceding source-workspace boundary proves private object metadata, exact
downloaded bytes, and FFprobe audio evidence while retaining a local path only
inside a context manager. This module adds exactly one durable decision before
any future CPU/GPU process may use that path:

``acknowledged lease + validated source -> short leased-to-running transaction``

It deliberately does not run Demucs, build a command, renew a lease, upload a
stem, write a terminal result, consume another RabbitMQ message, sleep, catch
task errors, or create a Kubernetes resource. Those follow-on tasks must run
inside the context yielded here and retain their own explicit state decisions.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.acknowledged_lease_preflight import DemucsAcknowledgedLeaseSourceWorkspace
from app.preflight_task_start import (
    DemucsRunningSource,
    DemucsTaskStartDatabase,
    start_preflight_validated_demucs_task,
)


class DemucsRunningSourceWorkspaceProtocolError(RuntimeError):
    """A safe category for an invalid durable/local handoff pairing.

    This category deliberately contains no task IDs, lease tokens, scratch
    paths, database diagnostics, or source-object coordinates. A future worker
    supervisor may expose this stable fact without leaking private evidence.
    """


@dataclass(frozen=True)
class DemucsRunningSourceWorkspace:
    """A current running lease paired with its still-open temporary source file.

    ``running`` proves PostgreSQL committed the guarded ``leased -> running``
    transition. ``source_path`` is valid only while the outer acknowledged
    source-workspace context remains open; it must never be persisted or logged.
    A future model command must consume it in that scope and later use
    ``running.lease`` for every renewal/completion guard.
    """

    running: DemucsRunningSource
    source_path: Path

    def __post_init__(self) -> None:
        """Reject a hand-built result before a future model process starts."""

        if not isinstance(self.running, DemucsRunningSource):
            raise TypeError("Demucs running source workspace requires a committed running source.")
        if not isinstance(self.source_path, Path):
            raise TypeError("Demucs running source workspace path is invalid.")


def _running_source_matches_workspace(
    running: DemucsRunningSource,
    workspace: DemucsAcknowledgedLeaseSourceWorkspace,
) -> bool:
    """Require the database result to preserve the exact owned source evidence.

    The existing start composition builds its result from
    ``workspace.preflight``, so matching values are the expected normal path.
    Rechecking both values here means a future alternate database adapter
    cannot accidentally authorize a model against another task or source after
    the local path has already been opened.
    """

    return running.lease == workspace.lease and running.source == workspace.source


@contextmanager
def opened_running_demucs_source_workspace(
    *,
    database: DemucsTaskStartDatabase,
    workspace: DemucsAcknowledgedLeaseSourceWorkspace,
) -> Iterator[DemucsRunningSourceWorkspace | None]:
    """Yield model-eligible input only after the current lease commits as running.

    ``None`` is a normal, durable ownership-loss outcome: PostgreSQL found no
    current unexpired row for this lease token. The caller must simply leave its
    enclosing source-workspace scope—there is no model command, artifact write,
    or retry decision in this function. A non-``None`` result holds exactly the
    original temporary path, so downstream CPU work cannot substitute another
    local source after the database transition.
    """

    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    if not isinstance(workspace, DemucsAcknowledgedLeaseSourceWorkspace):
        raise TypeError("workspace must be DemucsAcknowledgedLeaseSourceWorkspace.")

    # This call opens and closes one short PostgreSQL transaction before the
    # generator yields. It never holds a database lock while a later caller
    # performs CPU/GPU work with the temporary local media file.
    running = start_preflight_validated_demucs_task(
        database=database,
        preflight=workspace.preflight,
    )
    if running is None:
        yield None
        return
    if not isinstance(running, DemucsRunningSource) or not _running_source_matches_workspace(running, workspace):
        raise DemucsRunningSourceWorkspaceProtocolError("Demucs running source handoff is invalid.")

    yield DemucsRunningSourceWorkspace(
        running=running,
        source_path=workspace.source_path,
    )
