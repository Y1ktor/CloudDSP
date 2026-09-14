"""Unit tests for narrow ADTOF outer-worker failure classification.

These tests instantiate only local reviewed exception objects. They perform no
RabbitMQ/PostgreSQL/MinIO/model/image/Kubernetes operation and prove unknown or
task-integrity errors cannot be silently turned into generic retry behavior.
"""

from __future__ import annotations

import unittest

from app.adtof_cpu_process import ADTOFCPUProcessFailed, ADTOFCPUProcessUnavailable
from app.amqp_channel import ADTOFAMQPChannelUnavailable
from app.amqp_connection import ADTOFAMQPConfigurationError, ADTOFAMQPConnectionUnavailable
from app.amqp_manual_ack import ADTOFAMQPUnavailable
from app.minio_client import ADTOFMinioConfigurationError
from app.minio_upload import ADTOFUploadUnavailable
from app.output_artifact_head_object import ADTOFOutputHeadObjectUnavailable
from app.postgresql import ADTOFDatabaseConfigurationError, ADTOFDatabaseUnavailable
from app.stem_download import ADTOFStemDownloadConsistencyError, ADTOFStemDownloadUnavailable
from app.stem_object import ADTOFStemStorageProtocolError, ADTOFStemStorageUnavailable
from app.supervisor_backoff import ADTOFSupervisorEvent
from app.supervisor_failure_classification import classify_adtof_supervisor_failure


class ADTOFSupervisorFailureClassificationTests(unittest.TestCase):
    """Prove generic supervision has only narrow fatal/retryable authority."""

    def test_static_configuration_or_missing_worker_entrypoint_is_fatal(self) -> None:
        """Waiting cannot correct a bad Secret/topology/dependency/image contract."""

        failures = (
            ADTOFAMQPConfigurationError("safe test configuration category"),
            ADTOFDatabaseConfigurationError("safe test configuration category"),
            ADTOFMinioConfigurationError("safe test configuration category"),
            ADTOFCPUProcessUnavailable("safe test executable category"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_adtof_supervisor_failure(failure),
                    ADTOFSupervisorEvent.FATAL_CONFIGURATION,
                )

    def test_reviewed_broker_database_and_minio_availability_is_retryable(self) -> None:
        """Only explicit temporary wrappers may use future bounded backoff."""

        failures = (
            ADTOFAMQPConnectionUnavailable("safe test broker outage"),
            ADTOFAMQPChannelUnavailable("safe test broker outage"),
            ADTOFAMQPUnavailable("safe test broker outage"),
            ADTOFDatabaseUnavailable("safe test database outage"),
            ADTOFStemStorageUnavailable("safe test MinIO outage"),
            ADTOFStemDownloadUnavailable("safe test MinIO outage"),
            ADTOFUploadUnavailable("safe test MinIO outage"),
            ADTOFOutputHeadObjectUnavailable("safe test MinIO outage"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_adtof_supervisor_failure(failure),
                    ADTOFSupervisorEvent.RETRYABLE_FAILURE,
                )

    def test_integrity_model_and_unknown_errors_remain_unclassified(self) -> None:
        """They require durable task policy, not an unsafe generic worker retry."""

        failures = (
            ADTOFStemStorageProtocolError("safe test metadata mismatch"),
            ADTOFStemDownloadConsistencyError("safe test checksum mismatch"),
            ADTOFCPUProcessFailed("safe test model failure"),
            RuntimeError("safe test unknown failure"),
            KeyboardInterrupt(),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIsNone(classify_adtof_supervisor_failure(failure))

    def test_non_exception_input_is_rejected_without_message_inspection(self) -> None:
        """Arbitrary user-controlled data cannot manufacture a policy event."""

        with self.assertRaises(TypeError):
            classify_adtof_supervisor_failure(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
