"""Classify only safe outer-runtime failures for Basic Pitch supervision.

The future long-running worker will catch an exception around one complete fair
work-source iteration.  This pure module translates only a deliberately small
set of *worker-level* failures into the existing supervisor events:

* static Secret/topology/image-client configuration errors are fatal; and
* RabbitMQ connection/channel/receive and PostgreSQL availability faults are
  retryable after the supervisor's bounded backoff.

It intentionally does not classify task-specific MinIO, model, MIDI-output,
contract, protocol, or integrity failures.  Pre-model stem outages already
have their own durable task retry path; a model or post-model failure occurs
after a task may be ``running`` and needs its own reviewed task lifecycle
policy. Treating either as a generic worker restart would hide work rather
than recover it safely.

This module imports no network client, opens no connection, sleeps, catches an
exception, mutates a task, or changes Kubernetes state.  It merely maps an
exception object supplied by a later runtime to a finite policy fact.
"""

from __future__ import annotations

from app.amqp_channel import BasicPitchAMQPChannelUnavailable
from app.amqp_connection import BasicPitchAMQPConfigurationError, BasicPitchAMQPConnectionUnavailable
from app.amqp_manual_ack import BasicPitchAMQPUnavailable
from app.basic_pitch_process import BasicPitchProcessUnavailable
from app.minio_client import BasicPitchMinioConfigurationError
from app.postgresql import BasicPitchDatabaseConfigurationError, BasicPitchDatabaseUnavailable
from app.supervisor_backoff import BasicPitchSupervisorEvent


# These exceptions prove that this Pod's static configuration, mounted Secret,
# pinned client dependency, or image-provided executable is unusable. Waiting
# cannot correct them; Kubernetes should surface/restart the failed Pod after
# the manifest/image/Secret is fixed rather than spinning a hidden retry loop.
_FATAL_CONFIGURATION_ERRORS = (
    BasicPitchAMQPConfigurationError,
    BasicPitchDatabaseConfigurationError,
    BasicPitchMinioConfigurationError,
    BasicPitchProcessUnavailable,
)

# These safe wrappers are created only by bounded connection/channel/receive
# or restricted database transaction boundaries. They contain no raw endpoint
# or credential diagnostic and an outer retry is safe because no task-specific
# result is manufactured by merely classifying the fault.
_RETRYABLE_RUNTIME_ERRORS = (
    BasicPitchAMQPConnectionUnavailable,
    BasicPitchAMQPChannelUnavailable,
    BasicPitchAMQPUnavailable,
    BasicPitchDatabaseUnavailable,
)


def classify_basic_pitch_supervisor_failure(
    error: BaseException,
) -> BasicPitchSupervisorEvent | None:
    """Return a finite outer-runtime event, or ``None`` for task-specific risk.

    ``None`` is intentional. It tells the future runtime to preserve and
    expose the original error rather than choosing a generic sleep/restart for
    a task state it does not understand. The function does not inspect error
    messages, causes, tracebacks, object keys, or credentials, so those values
    cannot influence the policy or leak into its compact classification.
    """

    if not isinstance(error, BaseException):
        raise TypeError("Basic Pitch supervisor failure must be an exception.")
    if isinstance(error, _FATAL_CONFIGURATION_ERRORS):
        return BasicPitchSupervisorEvent.FATAL_CONFIGURATION
    if isinstance(error, _RETRYABLE_RUNTIME_ERRORS):
        return BasicPitchSupervisorEvent.RETRYABLE_FAILURE
    return None
