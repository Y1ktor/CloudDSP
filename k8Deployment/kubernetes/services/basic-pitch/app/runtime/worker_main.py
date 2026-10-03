"""Provide the executable, signal-aware process boundary for Basic Pitch.

The bootstrap entrypoint already knows how to validate mounted configuration,
construct the restricted clients, and run the worker runtime.  This module
adds only the last process concern that belongs at a container boundary:
Kubernetes sends ``SIGTERM`` during graceful Pod termination, and an
interactive local run can send ``SIGINT``.  Both signals set one in-memory
``threading.Event``.  The runtime sees that event through its small
``BasicPitchShutdownWaiter`` protocol, finishes its current bounded operation,
closes its RabbitMQ connection, and exits normally.

The signal handler intentionally performs no I/O, database work, MinIO call,
RabbitMQ acknowledgement, or logging.  Python signal handlers run in an
asynchronous context, so keeping it to ``Event.set()`` makes shutdown
cooperative and leaves durable work decisions to the existing worker logic.

This module is suitable for a later explicit image command such as
``python -m app.runtime.worker_main``.  It does not build an image, declare a
Kubernetes resource, add a health endpoint, or add reconnect-after-failure
behavior; each remains a separate deployment/lifecycle task.
"""

from __future__ import annotations

import signal
import sys
from dataclasses import dataclass
from threading import Event
from types import FrameType

from app.messaging.amqp_connection import BasicPitchAMQPConfigurationError
from app.artifacts.minio_client import BasicPitchMinioConfigurationError
from app.db.postgresql import BasicPitchDatabaseConfigurationError
from app.runtime.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    BasicPitchWorkerEntrypointConfigurationError,
    run_basic_pitch_worker_entrypoint,
)


# These are the static, safe categories that can occur while the bootstrap
# reads mounted settings or checks the required emptyDir.  The ordinary stderr
# line below intentionally does not print exception text: a lower-level
# implementation detail must not accidentally reveal a mounted Secret or
# internal service coordinate in normal container logs.
_BOOTSTRAP_CONFIGURATION_ERRORS = (
    BasicPitchAMQPConfigurationError,
    BasicPitchDatabaseConfigurationError,
    BasicPitchMinioConfigurationError,
    BasicPitchWorkerEntrypointConfigurationError,
)


@dataclass(frozen=True)
class BasicPitchEventShutdownWaiter:
    """Adapt one process-local ``Event`` to the runtime shutdown protocol.

    ``Event.wait`` returns exactly the bool shape required by
    ``BasicPitchShutdownWaiter``: ``True`` when a signal already arrived or
    arrives before the timeout, and ``False`` when the finite idle/backoff
    timeout elapsed.  It conveys no task data and owns no signal installation;
    that separation lets unit tests supply deterministic waiters elsewhere.
    """

    shutdown_event: Event

    def __post_init__(self) -> None:
        """Reject look-alike objects before they control the worker's shutdown path."""

        if not isinstance(self.shutdown_event, Event):
            raise TypeError("shutdown_event must be threading.Event.")

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait at most one runtime-provided delay for a cooperative stop request."""

        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be a number.")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative.")
        # The caller's supervisor policy has already bounded its timeout.  We
        # deliberately do not sleep separately or reinterpret a timeout here.
        return self.shutdown_event.wait(timeout_seconds)


def install_basic_pitch_shutdown_handlers(shutdown_event: Event) -> None:
    """Turn SIGTERM/SIGINT into an Event without doing work in signal context.

    The later Deployment's termination grace period gives the runtime time to
    notice this flag.  A signal received before RabbitMQ opens causes the
    runtime to skip that connection; one received during a bounded wait ends
    that wait promptly.  If a signal interrupts a model process, the existing
    subprocess boundary owns its process-group cleanup and durable task state.
    """

    if not isinstance(shutdown_event, Event):
        raise TypeError("shutdown_event must be threading.Event.")

    def request_shutdown(_signal_number: int, _current_frame: FrameType | None) -> None:
        """Record the request only; normal worker code performs all cleanup."""

        shutdown_event.set()

    # Register both forms so Pod deletion and Ctrl+C use one coherent shutdown
    # path.  Python requires this function to run on the process main thread;
    # a misconfigured wrapper lets that programming error surface rather than
    # silently leaving the worker unable to terminate gracefully.
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)


def main() -> int:
    """Run the one Basic Pitch worker process and return its explicit status.

    Static mounted-configuration failures receive the conventional ``78``
    status selected by the bootstrap layer.  Other exceptions deliberately
    propagate after their own runtime cleanup: this worker has no reviewed
    policy yet for masking task-specific faults or reconnecting a failed outer
    broker connection.  A container runtime converts an uncaught exception to
    a nonzero exit and Kubernetes applies the later Deployment restart policy.
    """

    shutdown_event = Event()
    install_basic_pitch_shutdown_handlers(shutdown_event)
    try:
        result = run_basic_pitch_worker_entrypoint(
            shutdown_waiter=BasicPitchEventShutdownWaiter(shutdown_event)
        )
    except _BOOTSTRAP_CONFIGURATION_ERRORS:
        # This stable category helps a local learner distinguish a bad mounted
        # Secret/ConfigMap/volume from a transient workload failure without
        # emitting the underlying exception's potentially sensitive details.
        print("basic-pitch worker has invalid or incomplete configuration.", file=sys.stderr)
        return EXIT_STATUS_CONFIGURATION_ERROR
    return result.exit_status


if __name__ == "__main__":
    # Keep `main()` directly testable; only the module execution boundary asks
    # Python to translate the returned integer into the container exit status.
    raise SystemExit(main())
