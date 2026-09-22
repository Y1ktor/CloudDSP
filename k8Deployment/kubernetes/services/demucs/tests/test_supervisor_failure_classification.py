"""Unit tests for narrow Demucs outer-worker failure classification.

Only local reviewed exception objects are constructed. No RabbitMQ,
PostgreSQL, MinIO, FFprobe/Demucs process, image, or Kubernetes action occurs;
unknown and task-integrity errors must not become generic backoff behavior.
"""

from __future__ import annotations

import unittest

from app.amqp_channel import DemucsAMQPChannelUnavailable
from app.amqp_connection import DemucsAMQPConfigurationError, DemucsAMQPConnectionUnavailable
from app.amqp_manual_ack import DemucsAMQPUnavailable
from app.demucs_artifact_upload import DemucsArtifactUploadConsistencyError, DemucsArtifactUploadUnavailable
from app.demucs_process import DemucsProcessFailed, DemucsProcessUnavailable
from app.ffprobe_process import DemucsFFprobeUnavailable
from app.minio_client import DemucsMinioConfigurationError
from app.postgresql import DemucsDatabaseConfigurationError, DemucsDatabaseUnavailable
from app.source_download import DemucsSourceDownloadConsistencyError, DemucsSourceDownloadUnavailable
from app.source_object import DemucsSourceStorageProtocolError, DemucsSourceStorageUnavailable
from app.supervisor_backoff import DemucsSupervisorEvent
from app.supervisor_failure_classification import classify_demucs_supervisor_failure


class DemucsSupervisorFailureClassificationTests(unittest.TestCase):
    """Prove generic supervision has only narrow fatal/retryable authority."""

    def test_static_configuration_or_missing_required_image_executable_is_fatal(self) -> None:
        """Waiting cannot correct bad settings, topology, or image content."""

        failures = (
            DemucsAMQPConfigurationError("safe test configuration category"),
            DemucsDatabaseConfigurationError("safe test configuration category"),
            DemucsMinioConfigurationError("safe test configuration category"),
            DemucsFFprobeUnavailable("safe test executable category"),
            DemucsProcessUnavailable("safe test executable category"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_demucs_supervisor_failure(failure),
                    DemucsSupervisorEvent.FATAL_CONFIGURATION,
                )

    def test_reviewed_broker_database_and_minio_availability_is_retryable(self) -> None:
        """Only explicit bounded dependency wrappers authorize process backoff."""

        failures = (
            DemucsAMQPConnectionUnavailable("safe test broker outage"),
            DemucsAMQPChannelUnavailable("safe test broker outage"),
            DemucsAMQPUnavailable("safe test broker outage"),
            DemucsDatabaseUnavailable("safe test database outage"),
            DemucsSourceStorageUnavailable("safe test MinIO outage"),
            DemucsSourceDownloadUnavailable("safe test MinIO outage"),
            DemucsArtifactUploadUnavailable("safe test MinIO outage"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_demucs_supervisor_failure(failure),
                    DemucsSupervisorEvent.RETRYABLE_FAILURE,
                )

    def test_integrity_model_and_unknown_errors_remain_unclassified(self) -> None:
        """They need durable task policy, not an unsafe generic process retry."""

        failures = (
            DemucsSourceStorageProtocolError("safe test metadata mismatch"),
            DemucsSourceDownloadConsistencyError("safe test source mismatch"),
            DemucsArtifactUploadConsistencyError("safe test artifact mismatch"),
            DemucsProcessFailed("safe test model failure"),
            RuntimeError("safe test unknown failure"),
            KeyboardInterrupt(),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIsNone(classify_demucs_supervisor_failure(failure))

    def test_non_exception_input_is_rejected_without_message_inspection(self) -> None:
        """Arbitrary data cannot manufacture a supervisor policy event."""

        with self.assertRaises(TypeError):
            classify_demucs_supervisor_failure(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
