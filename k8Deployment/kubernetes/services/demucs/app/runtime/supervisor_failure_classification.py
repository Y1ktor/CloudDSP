"""Classify only reviewed outer-worker Demucs failures for supervision.

A future long-running worker will catch an exception around one bounded worker
cycle. This pure boundary maps only established worker-level categories to the
events already understood by ``supervisor_backoff.py``:

* invalid static configuration or a missing required image executable is fatal;
* bounded RabbitMQ, PostgreSQL, and MinIO availability wrappers are retryable.

Every other error returns ``None``. Malformed request/task evidence,
checksum/metadata inconsistencies, FFprobe/model failure/timeout, and unknown
errors must retain their original type for the existing durable task lifecycle
or later operator-visible policy. This function neither catches work errors nor
retries anything: it has no loop, sleep, connection, task mutation, or
Kubernetes behavior.
"""

from __future__ import annotations

from app.messaging.amqp_channel import DemucsAMQPChannelUnavailable
from app.messaging.amqp_connection import DemucsAMQPConfigurationError, DemucsAMQPConnectionUnavailable
from app.messaging.amqp_manual_ack import DemucsAMQPUnavailable
from app.artifacts.demucs_artifact_upload import DemucsArtifactUploadUnavailable
from app.processing.demucs_process import DemucsProcessUnavailable
from app.processing.ffprobe_process import DemucsFFprobeUnavailable
from app.artifacts.minio_client import DemucsMinioConfigurationError
from app.db.postgresql import DemucsDatabaseConfigurationError, DemucsDatabaseUnavailable
from app.artifacts.source_download import DemucsSourceDownloadUnavailable
from app.artifacts.source_object import DemucsSourceStorageUnavailable
from app.runtime.supervisor_backoff import DemucsSupervisorEvent


# These categories prove waiting cannot make this Pod correct. They describe an
# invalid mounted setting/topology contract or an image missing one of its two
# required executables. The future runtime should exit visibly so Kubernetes
# reports/restarts the Pod only after an operator fixes the static problem.
_FATAL_CONFIGURATION_ERRORS = (
    DemucsAMQPConfigurationError,
    DemucsDatabaseConfigurationError,
    DemucsMinioConfigurationError,
    DemucsFFprobeUnavailable,
    DemucsProcessUnavailable,
)

# Each category is intentionally created by a restricted adapter after a
# bounded external operation. Classification uses only type—not exception text
# or cause—so credentials, endpoints, object keys, and SDK/driver diagnostics
# cannot steer policy or leak into a compact worker metric.
_RETRYABLE_AVAILABILITY_ERRORS = (
    DemucsAMQPConnectionUnavailable,
    DemucsAMQPChannelUnavailable,
    DemucsAMQPUnavailable,
    DemucsDatabaseUnavailable,
    DemucsSourceStorageUnavailable,
    DemucsSourceDownloadUnavailable,
    DemucsArtifactUploadUnavailable,
)


def classify_demucs_supervisor_failure(error: BaseException) -> DemucsSupervisorEvent | None:
    """Return a reviewed supervision event or preserve unsafe errors unchanged.

    ``None`` deliberately grants no generic recovery action. A later runtime
    may use a retryable event only after the surrounding task/recovery policy
    has established which durable work was committed or remains recoverable;
    this classifier never claims a broker acknowledgement was undone or a task
    transition was made.
    """

    if not isinstance(error, BaseException):
        raise TypeError("Demucs supervisor failure must be an exception.")
    if isinstance(error, _FATAL_CONFIGURATION_ERRORS):
        return DemucsSupervisorEvent.FATAL_CONFIGURATION
    if isinstance(error, _RETRYABLE_AVAILABILITY_ERRORS):
        return DemucsSupervisorEvent.RETRYABLE_FAILURE
    return None
