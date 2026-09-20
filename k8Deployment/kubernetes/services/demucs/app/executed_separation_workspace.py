"""Run one fixed Demucs command inside an already committed running workspace.

The preceding running-source workspace proves a PostgreSQL-committed
``running`` lease and retains one FFprobe-validated, temporary ``source.media``
path.  This module adds exactly the next local-model boundary:

``running workspace -> private empty output directory -> fixed CPU command -> zero exit``

It deliberately yields the model output only inside another context manager.
That lets the next *separate* local-artifact validation task inspect it before
the directory is removed.  A zero exit is not a successful task: this module
does not inventory stems, calculate hashes, call MinIO, mutate PostgreSQL,
receive or acknowledge RabbitMQ deliveries, or create any Kubernetes resource.
When the enclosing one-task runtime supplies its restricted database adapter,
this boundary does make periodic *short* lease-renewal transactions while it
waits for the model child; it never holds one across CPU work.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.demucs_command import (
    DemucsSeparationCommand,
    build_demucs_separation_command,
    resolved_demucs_work_directory,
)
from app.demucs_process import (
    DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    DemucsProcessRunner,
    run_demucs_separation,
    run_demucs_separation_with_lease_renewal,
)
from app.preflight_task_start import DemucsRunningSource
from app.running_lease_renewal import (
    DemucsRunningLeaseRenewalOutcome,
    renew_running_demucs_lease,
)
from app.running_source_workspace import DemucsRunningSourceWorkspace
from app.task_maintenance import DemucsTaskMaintenanceDatabase


class DemucsExecutedSeparationWorkspaceProtocolError(RuntimeError):
    """A caller tried to pair a model result with a different running task.

    The message intentionally has no lease token, task ID, local path, command,
    or model diagnostics. A later outer supervisor may map this stable
    programming/protocol fact without exposing private workload evidence.
    """


@dataclass(frozen=True)
class DemucsExecutedSeparationWorkspace:
    """One zero-exit separation and its still-current running-task ownership.

    ``separation`` contains the private source/output paths required by the
    next artifact-verification layer. Both paths are valid only while this
    context and the enclosing running-source context remain open. Nothing in
    this dataclass is proof that an expected stem exists, that a task succeeded,
    or that any object has been written to MinIO.
    """

    running_workspace: DemucsRunningSourceWorkspace
    separation: DemucsSeparationCommand

    def __post_init__(self) -> None:
        """Preserve the exact task mode/source pairing after the child exits."""

        if not isinstance(self.running_workspace, DemucsRunningSourceWorkspace):
            raise TypeError("Demucs executed separation requires a running workspace.")
        if not isinstance(self.separation, DemucsSeparationCommand):
            raise TypeError("Demucs executed separation command is invalid.")
        try:
            # macOS may expose the same temporary volume through both ``/var``
            # and ``/private/var``. Compare canonical existing paths so that
            # benign platform aliases cannot break the handoff, while the
            # command builder still rejects every path outside Pod scratch.
            canonical_workspace_source = self.running_workspace.source_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise DemucsExecutedSeparationWorkspaceProtocolError(
                "Demucs executed separation workspace is invalid."
            ) from error
        if (
            self.separation.stem_mode != self.running_workspace.running.lease.stem_mode
            or self.separation.source_path != canonical_workspace_source
        ):
            raise DemucsExecutedSeparationWorkspaceProtocolError(
                "Demucs executed separation workspace is invalid."
            )

    @property
    def running(self) -> DemucsRunningSource:
        """Expose committed ownership without duplicating the lease/token value."""

        return self.running_workspace.running


def _running_workspace_or_raise(value: object) -> DemucsRunningSourceWorkspace:
    """Require the prior committed-running handoff before model allocation."""

    if not isinstance(value, DemucsRunningSourceWorkspace):
        raise TypeError("workspace must be DemucsRunningSourceWorkspace.")
    return value


@contextmanager
def opened_executed_demucs_separation_workspace(
    workspace: DemucsRunningSourceWorkspace,
    *,
    work_directory: Path,
    timeout_seconds: int = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    runner: DemucsProcessRunner | None = None,
    renewal_database: DemucsTaskMaintenanceDatabase | None = None,
) -> Iterator[DemucsExecutedSeparationWorkspace]:
    """Run one approved CPU separation and scope its output to this ``with`` block.

    The caller must invoke this only inside
    ``opened_running_demucs_source_workspace(...)`` while its temporary source
    still exists. A random ``demucs-output-`` child keeps model output separate
    from the random source-download child and from every other task. The
    command builder validates both paths again; the bounded process adapter
    rebuilds the command immediately before execution. When the runtime supplies
    ``renewal_database``, the renewal-aware process entrypoint checks the same
    committed running lease no less often than its reviewed cadence and stops
    the real child before reporting ownership loss. ``TemporaryDirectory``
    removes the output after normal return, process failure, validation failure,
    renewal stop, or a future graceful shutdown exception.

    The yielded value permits only the next local stem-inventory boundary. It
    performs no artifact upload or durable state transition, so a zero exit
    must never be presented as a finished CloudDSP job.
    """

    running_workspace = _running_workspace_or_raise(workspace)
    if renewal_database is not None and not callable(getattr(renewal_database, "write_cursor", None)):
        # Validate before allocating output or starting a child. The periodic
        # callback would discover the same issue later, but discovering it then
        # would waste CPU and leave a shorter ownership margin unnecessarily.
        raise TypeError("renewal_database must provide write_cursor.")
    # Validate before ``TemporaryDirectory`` receives the caller path. This
    # means an invalid or symlinked scratch root cannot be used as a parent for
    # even an ephemeral output directory.
    validated_work_directory = resolved_demucs_work_directory(work_directory)
    with tempfile.TemporaryDirectory(
        prefix="demucs-output-",
        dir=validated_work_directory,
    ) as temporary_output_directory:
        separation = build_demucs_separation_command(
            stem_mode=running_workspace.running.lease.stem_mode,
            source_path=running_workspace.source_path,
            output_directory=Path(temporary_output_directory),
            work_directory=validated_work_directory,
        )
        # The outer source workspace remains immutable, but a successful
        # PostgreSQL renewal produces a new lease-expiry value. Keep it in this
        # local variable and carry that exact refreshed evidence to downstream
        # upload/completion guards after the child exits.
        active_running = running_workspace.running
        if renewal_database is None:
            completed_separation = run_demucs_separation(
                separation,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            executed_running_workspace = running_workspace
        else:
            def renewal_checkpoint() -> bool:
                """Commit one renewal and report whether the child may continue."""

                nonlocal active_running
                renewal = renew_running_demucs_lease(
                    database=renewal_database,
                    running=active_running,
                )
                if renewal.outcome is DemucsRunningLeaseRenewalOutcome.OWNERSHIP_LOST:
                    return False
                if (
                    renewal.outcome is not DemucsRunningLeaseRenewalOutcome.RENEWED
                    or renewal.running is None
                ):
                    raise RuntimeError("Demucs running lease-renewal result is invalid.")
                active_running = renewal.running
                return True

            completed_separation = run_demucs_separation_with_lease_renewal(
                separation,
                renewal_checkpoint=renewal_checkpoint,
                timeout_seconds=timeout_seconds,
                # A supplied runner must implement the stricter renewal-aware
                # protocol. The public process boundary validates that before
                # launching work; a plain test/production runner cannot be
                # silently threaded around an ownership-loss stop signal.
                runner=runner,  # type: ignore[arg-type]
            )
            executed_running_workspace = DemucsRunningSourceWorkspace(
                running=active_running,
                source_path=running_workspace.source_path,
            )
        yield DemucsExecutedSeparationWorkspace(
            running_workspace=executed_running_workspace,
            separation=completed_separation,
        )
