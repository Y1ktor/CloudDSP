"""Unit tests for narrow Basic Pitch outer-runtime failure classification.

The tests instantiate only safe local exception categories. They make no
RabbitMQ/PostgreSQL/MinIO/model/Docker/Kubernetes request and prove that task
integrity or model failures cannot become an unsafe generic retry.
"""

from __future__ import annotations

import unittest

from app.messaging.amqp_channel import BasicPitchAMQPChannelUnavailable
from app.messaging.amqp_connection import BasicPitchAMQPConfigurationError, BasicPitchAMQPConnectionUnavailable
from app.messaging.amqp_manual_ack import BasicPitchAMQPUnavailable
from app.processing.basic_pitch_process import BasicPitchProcessFailed, BasicPitchProcessUnavailable
from app.artifacts.minio_client import BasicPitchMinioConfigurationError
from app.db.postgresql import BasicPitchDatabaseConfigurationError, BasicPitchDatabaseUnavailable
from app.artifacts.stem_download import BasicPitchStemDownloadUnavailable
from app.artifacts.stem_object import BasicPitchStemStorageUnavailable
from app.runtime.supervisor_backoff import BasicPitchSupervisorEvent
from app.runtime.supervisor_failure_classification import classify_basic_pitch_supervisor_failure


class BasicPitchSupervisorFailureClassificationTests(unittest.TestCase):
    """Prove generic supervision has only safe fatal/retryable authority."""

    def test_static_configuration_or_missing_worker_executable_is_fatal(self) -> None:
        """Waiting cannot repair a bad Secret/topology/dependency/image contract."""

        failures = (
            BasicPitchAMQPConfigurationError("safe test configuration category"),
            BasicPitchDatabaseConfigurationError("safe test configuration category"),
            BasicPitchMinioConfigurationError("safe test configuration category"),
            BasicPitchProcessUnavailable("safe test executable category"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_basic_pitch_supervisor_failure(failure),
                    BasicPitchSupervisorEvent.FATAL_CONFIGURATION,
                )

    def test_bounded_broker_and_database_outages_are_retryable(self) -> None:
        """The supervisor may apply its existing bounded backoff to these wrappers."""

        failures = (
            BasicPitchAMQPConnectionUnavailable("safe test broker outage"),
            BasicPitchAMQPChannelUnavailable("safe test broker outage"),
            BasicPitchAMQPUnavailable("safe test broker outage"),
            BasicPitchDatabaseUnavailable("safe test database outage"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIs(
                    classify_basic_pitch_supervisor_failure(failure),
                    BasicPitchSupervisorEvent.RETRYABLE_FAILURE,
                )

    def test_task_specific_storage_model_and_unknown_failures_remain_unclassified(self) -> None:
        """They need durable task policy, not a potentially work-losing worker restart."""

        failures = (
            BasicPitchStemStorageUnavailable("safe test stem outage"),
            BasicPitchStemDownloadUnavailable("safe test stem outage"),
            BasicPitchProcessFailed("safe test model failure"),
            RuntimeError("safe test unknown failure"),
            KeyboardInterrupt(),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.assertIsNone(classify_basic_pitch_supervisor_failure(failure))

    def test_non_exception_input_is_rejected_without_string_inspection(self) -> None:
        """A runtime cannot manufacture an event from arbitrary user-controlled data."""

        with self.assertRaises(TypeError):
            classify_basic_pitch_supervisor_failure(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
