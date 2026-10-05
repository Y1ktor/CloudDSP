"""Compose the restricted ADTOF worker dependencies into one process boundary.

This module is the deliberately small bridge between Kubernetes-mounted worker
configuration and the already-tested ADTOF supervisor loop.  It creates only
the three least-privilege runtime dependencies that the loop needs:

* a PostgreSQL adapter for short, guarded task transactions;
* a MinIO S3 client constrained to the private artifacts Service and bucket;
  and
* a prepared RabbitMQ channel constrained to the ADTOF request queue.

It also installs one scoped SIGTERM/SIGINT event while the AMQP session and
supervisor are active.  The loop returns only two normal terminal outcomes:
clean shutdown and fatal static configuration.  This entrypoint maps those to
portable process statuses without calling :func:`sys.exit`, so a later tiny
executable wrapper can remain testable and own only container-process logging.

The entrypoint does not build an image, declare a Deployment, create a
Kubernetes resource, expose a network endpoint, or widen any service identity.
Unexpected operational errors intentionally propagate after the AMQP context
has closed its channel and connection; treating incomplete processing as a
successful exit would hide work that PostgreSQL/RabbitMQ must recover.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.processing.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.messaging.amqp_session import opened_adtof_rabbitmq_session
from app.artifacts.minio_client import ADTOFMinioSettings, create_boto3_adtof_minio_client
from app.db.postgresql import PsycopgADTOFDatabase
from app.runtime.shutdown_event import installed_adtof_shutdown_waiter
from app.runtime.supervisor_loop import (
    ADTOFSupervisorLoopOutcome,
    ADTOFSupervisorLoopResult,
    run_adtof_supervisor_until_stop,
)
from app.runtime.supervisor_step import ADTOFSupervisorStepState


# This coordinate is deliberately fixed in source instead of supplied through
# an arbitrary environment variable.  A future Deployment must mount one
# writable, bounded ``emptyDir`` here.  A configurable path could redirect
# transient audio/MIDI files into a Secret volume, application source, or a
# host-like location by mistake.
DEFAULT_ADTOF_WORK_DIRECTORY = Path("/worker-scratch")

# POSIX ``sysexits.h`` reserves 78 for invalid configuration.  Returning this
# value makes a reviewed fatal setup/worker-configuration outcome visible to
# Kubernetes without this module deciding whether a container should restart.
EXIT_STATUS_SUCCESS = 0
EXIT_STATUS_CONFIGURATION_ERROR = 78


class ADTOFWorkerEntrypointConfigurationError(RuntimeError):
    """The mandatory ADTOF Pod-local scratch mount is absent or unsafe."""


@dataclass(frozen=True)
class ADTOFWorkerEntrypointResult:
    """One normal supervisor terminal result and its process exit status.

    The result retains only control-plane facts.  It intentionally does not
    retain a RabbitMQ channel, database adapter, MinIO client, Secret value,
    object key, local scratch path, or model output.
    """

    supervisor: ADTOFSupervisorLoopResult
    exit_status: int

    def __post_init__(self) -> None:
        """Require the status to be the one exact mapping for this outcome."""

        if not isinstance(self.supervisor, ADTOFSupervisorLoopResult):
            raise TypeError("ADTOF worker entrypoint supervisor result is invalid.")
        if type(self.exit_status) is not int:
            raise TypeError("ADTOF worker entrypoint exit status is invalid.")
        if self.exit_status != exit_status_for_adtof_supervisor(self.supervisor):
            raise ValueError("ADTOF worker entrypoint exit status does not match supervisor outcome.")


def _validated_work_directory(path: object = DEFAULT_ADTOF_WORK_DIRECTORY) -> Path:
    """Require the existing non-symlink scratch mount without creating it.

    The entrypoint must never silently create this path in its image layer.  A
    fabricated directory would hide a missing ``emptyDir`` mount and could
    place transient media on an unintended filesystem.  The later Deployment
    owns volume creation and mounting; this check only proves the fixed mount
    is a real directory before the worker opens its broker session.
    """

    if not isinstance(path, Path):
        raise ADTOFWorkerEntrypointConfigurationError(
            "ADTOF worker scratch directory configuration is invalid."
        )
    try:
        if path.is_symlink():
            raise ADTOFWorkerEntrypointConfigurationError(
                "ADTOF worker scratch directory configuration is invalid."
            )
        resolved = path.resolve(strict=True)
    except ADTOFWorkerEntrypointConfigurationError:
        raise
    except (OSError, RuntimeError) as error:
        raise ADTOFWorkerEntrypointConfigurationError(
            "ADTOF worker scratch directory configuration is invalid."
        ) from error
    if not resolved.is_dir():
        raise ADTOFWorkerEntrypointConfigurationError(
            "ADTOF worker scratch directory configuration is invalid."
        )
    return resolved


def exit_status_for_adtof_supervisor(supervisor: ADTOFSupervisorLoopResult) -> int:
    """Map a normal supervisor terminal outcome to a portable process status.

    A Kubernetes SIGTERM/SIGINT shutdown is a clean exit, while an explicitly
    classified fatal configuration outcome needs a visible configuration exit.
    Unhandled operational failures never reach this mapper: they escape the
    loop and AMQP context so Kubernetes can restart the process and durable
    PostgreSQL/RabbitMQ state can recover its work.
    """

    if not isinstance(supervisor, ADTOFSupervisorLoopResult):
        raise TypeError("supervisor must be ADTOFSupervisorLoopResult.")
    if supervisor.outcome is ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED:
        return EXIT_STATUS_SUCCESS
    if supervisor.outcome is ADTOFSupervisorLoopOutcome.EXIT_FATAL:
        return EXIT_STATUS_CONFIGURATION_ERROR
    raise ValueError("ADTOF supervisor loop outcome is invalid.")


def run_adtof_worker_entrypoint(
    *,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> ADTOFWorkerEntrypointResult:
    """Build restricted dependencies, run the supervisor, and return its status.

    The adapters validate their fixed local Service coordinates and mounted
    credentials before they use them.  PostgreSQL and Boto3 construction opens
    no database transaction or object request.  The scoped signal context then
    protects the complete AMQP-session/supervisor lifetime: a termination
    request interrupts bounded waits and prevents a new worker cycle, while
    the session's ``finally`` closes the channel before its connection.

    Configuration errors intentionally propagate rather than being converted
    to a false clean exit.  A later executable wrapper can report only their
    reviewed categories and return ``78``.  This composition itself does not
    call ``sys.exit`` or perform Kubernetes work.
    """

    # Resolve all non-AMQP static dependencies before the process opens a
    # RabbitMQ socket.  The database adapter validates its own fixed settings
    # during construction but opens a short connection only for actual task
    # transactions.  The MinIO factory similarly creates a client without an
    # S3 request.
    database = PsycopgADTOFDatabase()
    minio_settings = ADTOFMinioSettings.from_environment()
    storage_client = create_boto3_adtof_minio_client(minio_settings)
    work_directory = _validated_work_directory()

    # Keep signal handling narrower than module/process lifetime.  The context
    # restores the caller's former handlers after either a normal exit or an
    # exception, so tests and a future wrapper cannot inherit stale handlers.
    with installed_adtof_shutdown_waiter() as shutdown_waiter:
        # The session validates AMQP configuration, creates exactly one
        # private prefetch-one channel, and guarantees channel-then-connection
        # cleanup even when the supervisor propagates an unexpected error.
        with opened_adtof_rabbitmq_session() as channel:
            supervisor = run_adtof_supervisor_until_stop(
                channel,
                initial_state=ADTOFSupervisorStepState(),
                database=database,
                storage_client=storage_client,
                work_directory=work_directory,
                shutdown_waiter=shutdown_waiter,
                process_timeout_seconds=process_timeout_seconds,
                process_runner=process_runner,
                jitter_fraction=jitter_fraction,
            )

    return ADTOFWorkerEntrypointResult(
        supervisor=supervisor,
        exit_status=exit_status_for_adtof_supervisor(supervisor),
    )
