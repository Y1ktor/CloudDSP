"""Classify only reviewed outer-worker ADTOF failures for supervision.

The future long-running worker will catch an exception around one bounded
receive-and-execute iteration. This pure boundary maps only established
worker-level failure categories to the events already understood by
``supervisor_backoff.py``:

* bad static configuration or a missing image entrypoint is fatal; and
* bounded RabbitMQ, PostgreSQL, and MinIO availability wrappers are retryable.

Every other failure returns ``None``. In particular, malformed request/task
evidence, checksum/metadata inconsistency, model failure/timeout, and unknown
exceptions must retain their original type for a later durable task-lifecycle
policy. This function neither catches errors around work nor causes a retry:
it has no loop, sleep, connection, task mutation, or Kubernetes behavior.
"""

from __future__ import annotations

from app.processing.adtof_cpu_process import ADTOFCPUProcessUnavailable
from app.messaging.amqp_channel import ADTOFAMQPChannelUnavailable
from app.messaging.amqp_connection import ADTOFAMQPConfigurationError, ADTOFAMQPConnectionUnavailable
from app.messaging.amqp_manual_ack import ADTOFAMQPUnavailable
from app.artifacts.minio_client import ADTOFMinioConfigurationError
from app.artifacts.minio_upload import ADTOFUploadUnavailable
from app.artifacts.output_artifact_head_object import ADTOFOutputHeadObjectUnavailable
from app.db.postgresql import ADTOFDatabaseConfigurationError, ADTOFDatabaseUnavailable
from app.artifacts.stem_download import ADTOFStemDownloadUnavailable
from app.artifacts.stem_object import ADTOFStemStorageUnavailable
from app.runtime.supervisor_backoff import ADTOFSupervisorEvent


# These errors prove that waiting alone cannot make the current Pod usable: its
# mounted settings/topology contract is invalid or the approved image cannot
# start the expected Python entrypoint. The later runtime must make failure
# visible rather than conceal a deterministic configuration problem in backoff.
_FATAL_CONFIGURATION_ERRORS = (
    ADTOFAMQPConfigurationError,
    ADTOFDatabaseConfigurationError,
    ADTOFMinioConfigurationError,
    ADTOFCPUProcessUnavailable,
)

# Each wrapper is deliberately emitted by a restricted boundary after a
# bounded service operation. The classifier relies on the *type*, never error
# messages/causes, so credentials, endpoints, object keys, and SDK diagnostics
# cannot influence a policy decision or enter a concise worker metric.
_RETRYABLE_AVAILABILITY_ERRORS = (
    ADTOFAMQPConnectionUnavailable,
    ADTOFAMQPChannelUnavailable,
    ADTOFAMQPUnavailable,
    ADTOFDatabaseUnavailable,
    ADTOFStemStorageUnavailable,
    ADTOFStemDownloadUnavailable,
    ADTOFUploadUnavailable,
    ADTOFOutputHeadObjectUnavailable,
)


def classify_adtof_supervisor_failure(error: BaseException) -> ADTOFSupervisorEvent | None:
    """Return a reviewed supervision event, or preserve an unsafe unknown error.

    `None` deliberately means no generic recovery action is authorized by this
    layer. A later supervisor may use a retryable event only in conjunction
    with an explicit current-lease/recovery policy; this classifier does not
    claim that a broker acknowledgement has been undone or that a task state
    has been durably changed.
    """

    if not isinstance(error, BaseException):
        raise TypeError("ADTOF supervisor failure must be an exception.")
    if isinstance(error, _FATAL_CONFIGURATION_ERRORS):
        return ADTOFSupervisorEvent.FATAL_CONFIGURATION
    if isinstance(error, _RETRYABLE_AVAILABILITY_ERRORS):
        return ADTOFSupervisorEvent.RETRYABLE_FAILURE
    return None
