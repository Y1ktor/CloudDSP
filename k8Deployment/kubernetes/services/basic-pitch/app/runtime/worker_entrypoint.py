"""Bootstrap the local Basic Pitch worker from validated mounted configuration.

This module is the narrow composition between Kubernetes-injected environment
settings and the already-tested worker runtime.  It builds only the reviewed,
least-privilege dependencies:

* the private RabbitMQ consumer settings and connection factory inputs;
* the restricted Basic Pitch PostgreSQL transaction adapter; and
* the restricted MinIO S3 client for the one approved uploads bucket.

It also validates the fixed Pod-local scratch mount before any work can start,
then delegates all broker lifecycle and work processing to
``worker_runtime.py``.  A compact runtime exit reason maps to a conventional
process status without this module calling ``sys.exit`` itself.

Signal installation and the concrete executable ``main()`` remain intentionally
separate: callers must supply a shutdown-aware waiter so the entrypoint cannot
silently invent global signal behavior. Deployment volume/env wiring, image
CMD, reconnect-after-failure policy, health endpoints, and Kubernetes changes
are separate focused tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.messaging.amqp_connection import BasicPitchAMQPSettings
from app.processing.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.artifacts.minio_client import (
    BasicPitchMinioSettings,
    create_boto3_basic_pitch_minio_client,
)
from app.db.postgresql import PsycopgBasicPitchDatabase
from app.runtime.supervisor_action import BasicPitchShutdownWaiter
from app.runtime.worker_runtime import (
    BasicPitchWorkerExitReason,
    BasicPitchWorkerRuntimeResult,
    run_basic_pitch_worker_runtime,
)


# This path is deliberately source-level rather than a generic environment
# path. The later Deployment must mount one writable ``emptyDir`` here. Keeping
# the coordinate fixed prevents a ConfigMap typo from redirecting transient
# WAV/MIDI bytes to a credentials mount, application source, or host-like path.
DEFAULT_BASIC_PITCH_WORK_DIRECTORY = Path("/worker-scratch")

# POSIX `sysexits.h` reserves 78 for an invalid configuration. Returning this
# status lets Kubernetes report a deterministic fatal container exit without
# this module calling `sys.exit()` or deciding a restart policy itself.
EXIT_STATUS_SUCCESS = 0
EXIT_STATUS_CONFIGURATION_ERROR = 78


class BasicPitchWorkerEntrypointConfigurationError(RuntimeError):
    """The fixed Pod-local scratch mount is absent, unsafe, or incorrectly typed."""


@dataclass(frozen=True)
class BasicPitchWorkerEntrypointResult:
    """Committed runtime exit fact plus the status a future executable returns."""

    runtime: BasicPitchWorkerRuntimeResult
    exit_status: int

    def __post_init__(self) -> None:
        """Keep normal shutdown and fatal configuration status mappings exact."""

        if not isinstance(self.runtime, BasicPitchWorkerRuntimeResult):
            raise TypeError("Basic Pitch worker entrypoint runtime result is invalid.")
        if type(self.exit_status) is not int:
            raise TypeError("Basic Pitch worker entrypoint exit status is invalid.")
        expected_status = exit_status_for_basic_pitch_worker_runtime(self.runtime)
        if self.exit_status != expected_status:
            raise ValueError("Basic Pitch worker entrypoint exit status does not match runtime reason.")


def _validated_work_directory(path: object = DEFAULT_BASIC_PITCH_WORK_DIRECTORY) -> Path:
    """Require the one existing non-symlink scratch mount without creating it.

    The entrypoint must not quietly make a directory in the image filesystem:
    that would hide a missing `emptyDir` mount and could retain or misplace
    transient media. The later Deployment owns creation/mounting; this check
    only proves its expected fixed target is a real directory before MinIO or
    RabbitMQ work begins.
    """

    if not isinstance(path, Path):
        raise BasicPitchWorkerEntrypointConfigurationError(
            "Basic Pitch worker scratch directory configuration is invalid."
        )
    try:
        if path.is_symlink():
            raise BasicPitchWorkerEntrypointConfigurationError(
                "Basic Pitch worker scratch directory configuration is invalid."
            )
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BasicPitchWorkerEntrypointConfigurationError(
            "Basic Pitch worker scratch directory configuration is invalid."
        ) from error
    if not resolved.is_dir():
        raise BasicPitchWorkerEntrypointConfigurationError(
            "Basic Pitch worker scratch directory configuration is invalid."
        )
    return resolved


def exit_status_for_basic_pitch_worker_runtime(runtime: BasicPitchWorkerRuntimeResult) -> int:
    """Map a normal runtime exit to the explicit status for a future CLI main.

    Clean shutdown is a successful process exit. A runtime-detected static
    configuration failure returns POSIX's conventional configuration status;
    no error object, Secret, endpoint, or task evidence crosses this boundary.
    Unhandled errors do not enter this mapper—they propagate from the runtime
    after its broker cleanup so an outer process can report them accurately.
    """

    if not isinstance(runtime, BasicPitchWorkerRuntimeResult):
        raise TypeError("runtime must be BasicPitchWorkerRuntimeResult.")
    if runtime.reason is BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED:
        return EXIT_STATUS_SUCCESS
    if runtime.reason is BasicPitchWorkerExitReason.FATAL_CONFIGURATION:
        return EXIT_STATUS_CONFIGURATION_ERROR
    raise ValueError("Basic Pitch worker runtime exit reason is invalid.")


def run_basic_pitch_worker_entrypoint(
    *,
    shutdown_waiter: BasicPitchShutdownWaiter,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> BasicPitchWorkerEntrypointResult:
    """Build validated worker dependencies, run the loop, and return process status.

    Each `from_environment()` call validates fixed local Service/identity
    coordinates and Secret presence before a client uses its credential. The
    Boto3 factory constructs one private MinIO client without a request; the
    runtime later opens RabbitMQ and uses short PostgreSQL/MinIO operations as
    individual work boundaries. A malformed mounted setting or missing scratch
    directory raises its safe configuration category before the runtime starts.

    This function intentionally neither catches those configuration errors nor
    calls ``sys.exit``. A future thin executable wrapper can log the reviewed
    category and return 78, while tests can invoke this composition directly.
    """

    if not callable(getattr(shutdown_waiter, "wait_for_shutdown", None)):
        raise TypeError("shutdown_waiter must provide wait_for_shutdown.")

    # Construct dependencies in this order so all static environment validation
    # happens before the first RabbitMQ socket. `PsycopgBasicPitchDatabase`
    # resolves its own restricted settings in its constructor but opens no
    # connection until a later short transaction begins.
    amqp_settings = BasicPitchAMQPSettings.from_environment()
    database = PsycopgBasicPitchDatabase()
    minio_settings = BasicPitchMinioSettings.from_environment()
    storage_client = create_boto3_basic_pitch_minio_client(minio_settings)
    work_directory = _validated_work_directory()

    runtime = run_basic_pitch_worker_runtime(
        amqp_settings=amqp_settings,
        database=database,
        storage_client=storage_client,
        work_directory=work_directory,
        shutdown_waiter=shutdown_waiter,
        process_timeout_seconds=process_timeout_seconds,
        process_runner=process_runner,
        jitter_fraction=jitter_fraction,
    )
    return BasicPitchWorkerEntrypointResult(
        runtime=runtime,
        exit_status=exit_status_for_basic_pitch_worker_runtime(runtime),
    )
