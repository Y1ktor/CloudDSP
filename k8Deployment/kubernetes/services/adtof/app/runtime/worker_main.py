"""Provide the executable process boundary for the local ADTOF worker.

``worker_entrypoint.py`` already owns restricted dependency construction,
scoped SIGTERM/SIGINT installation, AMQP-session lifecycle, and supervisor-loop
execution.  This module is intentionally thinner: it turns only the known
static bootstrap-configuration categories into one non-sensitive container
diagnostic and returns the entrypoint's explicit exit status.

It does not catch workload, broker-availability, database, MinIO, model, or
unknown programming failures.  Those failures must remain visible after their
existing cleanup so Kubernetes can apply the future Deployment restart policy
and PostgreSQL/RabbitMQ can recover authoritative durable work.
"""

from __future__ import annotations

import sys

from app.messaging.amqp_connection import ADTOFAMQPConfigurationError
from app.artifacts.minio_client import ADTOFMinioConfigurationError
from app.db.postgresql import ADTOFDatabaseConfigurationError
from app.runtime.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    ADTOFWorkerEntrypointConfigurationError,
    run_adtof_worker_entrypoint,
)


# These are the only errors that can arise while bootstrap reads mounted
# configuration or verifies the required future ``emptyDir`` mount.  Their
# individual text can mention internal coordinates or variable names, so this
# container-facing boundary prints one stable category rather than exception
# details.  Static errors detected later inside the supervisor return the same
# explicit status through the entrypoint result instead of reaching this tuple.
_BOOTSTRAP_CONFIGURATION_ERRORS = (
    ADTOFAMQPConfigurationError,
    ADTOFDatabaseConfigurationError,
    ADTOFMinioConfigurationError,
    ADTOFWorkerEntrypointConfigurationError,
)


def main() -> int:
    """Run one ADTOF worker process and return its reviewed exit status.

    A clean SIGTERM/SIGINT shutdown returns ``0``.  Invalid mounted settings or
    an unsafe/missing scratch mount print one safe line and return POSIX
    configuration status ``78``.  All other exceptions deliberately propagate
    after the bootstrap entrypoint has allowed its context managers to close
    the AMQP channel and connection; this function must not convert incomplete
    processing into a misleading successful exit.
    """

    try:
        result = run_adtof_worker_entrypoint()
    except _BOOTSTRAP_CONFIGURATION_ERRORS:
        # Do not include the original exception text: it could contain a
        # private Service coordinate or accidentally reveal mounted-Secret
        # context in an ordinary Pod log.  The source-specific exception type
        # remains available to focused unit tests and callers that need it.
        print("adtof worker has invalid or incomplete configuration.", file=sys.stderr)
        return EXIT_STATUS_CONFIGURATION_ERROR
    return result.exit_status


if __name__ == "__main__":
    # Keep ``main`` directly unit-testable; only an actual module execution
    # asks Python to turn its returned integer into the container exit code.
    raise SystemExit(main())
