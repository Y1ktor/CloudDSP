"""Compose the restricted Demucs worker dependencies into one process boundary.

This module is the deliberately small bridge between Kubernetes-mounted worker
configuration and the already-tested Demucs supervisor loop.  It creates only
the runtime dependencies that loop needs:

* a PostgreSQL adapter for short, guarded task transactions;
* one MinIO S3 client constrained to the private uploads bucket, used through
  separate narrow read and artifact-write capabilities; and
* short-lived RabbitMQ sessions constrained to the Demucs request queue.

It also installs one scoped SIGTERM/SIGINT event while the session-owning
supervisor is active. Each normal polling turn obtains a fresh AMQP session;
recovery turns need no broker socket. The loop returns only two normal terminal
outcomes: clean shutdown and fatal static configuration. This entrypoint maps
those to portable process statuses without calling :func:`sys.exit`, so the
tiny executable wrapper can remain testable and own only container-process
logging.

The entrypoint does not build an image, declare a Deployment, create a
Kubernetes resource, expose a network endpoint, or widen any service identity.
Unexpected operational errors intentionally propagate after a session context
has closed its channel and connection; treating incomplete processing as a
successful exit would hide work that PostgreSQL/RabbitMQ must recover.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.artifacts.minio_client import DemucsMinioSettings, create_boto3_demucs_minio_client
from app.db.postgresql import PsycopgDemucsDatabase
from app.runtime.shutdown_event import installed_demucs_shutdown_waiter
from app.processing.source_preflight import DemucsSourcePreflightClient
from app.runtime.supervisor_loop import (
    DemucsSupervisorLoopOutcome,
    DemucsSupervisorLoopResult,
)
from app.runtime.supervisor_step import DemucsSupervisorStepState
from app.runtime.session_supervisor import run_demucs_session_supervisor_until_stop


# This coordinate is deliberately fixed in source instead of supplied through
# an arbitrary environment variable.  A future Deployment must mount one
# writable, bounded ``emptyDir`` here.  A configurable path could redirect
# transient audio/stem files into a Secret volume, application source, or a
# host-like location by mistake.
DEFAULT_DEMUCS_WORK_DIRECTORY = Path("/worker-scratch")

# POSIX ``sysexits.h`` reserves 78 for invalid configuration.  Returning this
# value makes a reviewed fatal setup/worker-configuration outcome visible to
# Kubernetes without this module deciding whether a container should restart.
EXIT_STATUS_SUCCESS = 0
EXIT_STATUS_CONFIGURATION_ERROR = 78


class DemucsWorkerEntrypointConfigurationError(RuntimeError):
    """The mandatory Demucs Pod-local scratch mount is absent or unsafe."""


@dataclass(frozen=True)
class DemucsWorkerEntrypointResult:
    """One normal supervisor terminal result and its process exit status.

    The result retains only control-plane facts.  It intentionally does not
    retain a RabbitMQ channel, database adapter, MinIO client, Secret value,
    object key, local scratch path, source audio, or model output.
    """

    supervisor: DemucsSupervisorLoopResult
    exit_status: int

    def __post_init__(self) -> None:
        """Require the status to be the one exact mapping for this outcome."""

        if not isinstance(self.supervisor, DemucsSupervisorLoopResult):
            raise TypeError("Demucs worker entrypoint supervisor result is invalid.")
        if type(self.exit_status) is not int:
            raise TypeError("Demucs worker entrypoint exit status is invalid.")
        if self.exit_status != exit_status_for_demucs_supervisor(self.supervisor):
            raise ValueError("Demucs worker entrypoint exit status does not match supervisor outcome.")


def _validated_work_directory(path: object = DEFAULT_DEMUCS_WORK_DIRECTORY) -> Path:
    """Require the existing non-symlink scratch mount without creating it.

    The entrypoint must never silently create this path in its image layer. A
    fabricated directory would hide a missing ``emptyDir`` mount and could
    place transient media on an unintended filesystem.  The later Deployment
    owns volume creation and mounting; this check only proves the fixed mount
    is a real directory before the worker opens its broker session.
    """

    if not isinstance(path, Path):
        raise DemucsWorkerEntrypointConfigurationError(
            "Demucs worker scratch directory configuration is invalid."
        )
    try:
        if path.is_symlink():
            raise DemucsWorkerEntrypointConfigurationError(
                "Demucs worker scratch directory configuration is invalid."
            )
        resolved = path.resolve(strict=True)
    except DemucsWorkerEntrypointConfigurationError:
        raise
    except (OSError, RuntimeError) as error:
        raise DemucsWorkerEntrypointConfigurationError(
            "Demucs worker scratch directory configuration is invalid."
        ) from error
    if not resolved.is_dir():
        raise DemucsWorkerEntrypointConfigurationError(
            "Demucs worker scratch directory configuration is invalid."
        )
    return resolved


def exit_status_for_demucs_supervisor(supervisor: DemucsSupervisorLoopResult) -> int:
    """Map a normal supervisor terminal outcome to a portable process status.

    A Kubernetes SIGTERM/SIGINT shutdown is a clean exit, while an explicitly
    classified fatal configuration outcome needs a visible configuration exit.
    Unhandled operational failures never reach this mapper: they escape the
    loop and AMQP context so Kubernetes can restart the process and durable
    PostgreSQL/RabbitMQ state can recover its work.
    """

    if not isinstance(supervisor, DemucsSupervisorLoopResult):
        raise TypeError("supervisor must be DemucsSupervisorLoopResult.")
    if supervisor.outcome is DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED:
        return EXIT_STATUS_SUCCESS
    if supervisor.outcome is DemucsSupervisorLoopOutcome.EXIT_FATAL:
        return EXIT_STATUS_CONFIGURATION_ERROR
    raise ValueError("Demucs supervisor loop outcome is invalid.")


def run_demucs_worker_entrypoint() -> DemucsWorkerEntrypointResult:
    """Build restricted dependencies, run the supervisor, and return its status.

    The adapters validate fixed local Service coordinates and mounted
    credentials before they use them.  PostgreSQL and Boto3 construction opens
    no database transaction or object request.  One Boto3 client is then cast
    into the two narrow structural capabilities that the loop accepts: source
    preflight/download and planned stem upload.  It cannot access a broader
    bucket because the future Pod receives only the restricted MinIO identity.

    The scoped signal context protects the complete AMQP-session/supervisor
    lifetime: a termination request interrupts bounded waits and prevents a
    new worker cycle, while the session's ``finally`` closes the channel before
    its connection.  Configuration errors intentionally propagate rather than
    being converted to a false clean exit.  A later executable wrapper can
    report only their reviewed categories and return ``78``.  This composition
    itself does not call ``sys.exit`` or perform Kubernetes work.
    """

    # Resolve all non-AMQP static dependencies before the process opens a
    # RabbitMQ socket.  The database adapter validates its own fixed settings
    # during construction but opens a short connection only for actual task
    # transactions.  The MinIO factory similarly creates a client without an
    # S3 request.
    database = PsycopgDemucsDatabase()
    minio_settings = DemucsMinioSettings.from_environment()
    storage_client = create_boto3_demucs_minio_client(minio_settings)
    work_directory = _validated_work_directory()

    # Boto3's one concrete S3 client supports both reviewed structural
    # protocols.  Keeping the casts here avoids handing a broad client type to
    # downstream business layers; their function signatures still expose only
    # the exact methods required for source reads or planned artifact writes.
    source_client = cast(DemucsSourcePreflightClient, storage_client)
    artifact_client = cast(DemucsPutObjectClient, storage_client)

    # Keep signal handling narrower than module/process lifetime. The session
    # supervisor creates/cleans an AMQP channel only for one normal `basic_get`
    # turn, so a stale idle socket cannot make a living Pod look active forever.
    # This context restores former handlers after normal exit or an exception.
    with installed_demucs_shutdown_waiter() as shutdown_waiter:
        supervisor = run_demucs_session_supervisor_until_stop(
            initial_state=DemucsSupervisorStepState(),
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            shutdown_waiter=shutdown_waiter,
        )

    return DemucsWorkerEntrypointResult(
        supervisor=supervisor,
        exit_status=exit_status_for_demucs_supervisor(supervisor),
    )
