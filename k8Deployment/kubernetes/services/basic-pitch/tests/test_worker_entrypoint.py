"""Unit tests for Basic Pitch's environment-bootstrap entrypoint composition.

All mounted-configuration factories and the runtime are patched. These tests
do not read real Secrets, open RabbitMQ/PostgreSQL/MinIO, sleep, run Basic
Pitch, install signals, call ``sys.exit``, build an image, or change Kubernetes.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.messaging.amqp_connection import (
    DEFAULT_BASIC_PITCH_AMQP_HOST,
    BasicPitchAMQPConfigurationError,
    BasicPitchAMQPSettings,
)
from app.artifacts.minio_client import (
    LOCAL_ARTIFACTS_BUCKET,
    LOCAL_MINIO_INTERNAL_ENDPOINT,
    LOCAL_S3_ADDRESSING_STYLE,
    LOCAL_S3_REGION,
    BasicPitchMinioSettings,
)
from app.runtime.supervisor_step import BasicPitchSupervisorStepState
from app.runtime.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    EXIT_STATUS_SUCCESS,
    BasicPitchWorkerEntrypointResult,
    BasicPitchWorkerEntrypointConfigurationError,
    _validated_work_directory,
    exit_status_for_basic_pitch_worker_runtime,
    run_basic_pitch_worker_entrypoint,
)
from app.runtime.worker_runtime import (
    BasicPitchWorkerExitReason,
    BasicPitchWorkerRuntimeResult,
)


def amqp_settings() -> BasicPitchAMQPSettings:
    """Return valid synthetic reviewed settings for patched factory assertions."""

    return BasicPitchAMQPSettings(
        host=DEFAULT_BASIC_PITCH_AMQP_HOST,
        port=5672,
        username="clouddsp-basic-pitch",
        password="not-a-real-password",
    )


def minio_settings() -> BasicPitchMinioSettings:
    """Return valid synthetic restricted MinIO settings without a real Secret."""

    return BasicPitchMinioSettings(
        internal_endpoint=LOCAL_MINIO_INTERNAL_ENDPOINT,
        artifacts_bucket=LOCAL_ARTIFACTS_BUCKET,
        region_name=LOCAL_S3_REGION,
        addressing_style=LOCAL_S3_ADDRESSING_STYLE,
        access_key="not-a-real-access-key",
        secret_key="not-a-real-secret-key",
    )


def runtime_result(reason: BasicPitchWorkerExitReason) -> BasicPitchWorkerRuntimeResult:
    """Return one compact normal runtime exit for process-status mapping."""

    return BasicPitchWorkerRuntimeResult(
        reason=reason,
        completed_steps=4,
        final_state=BasicPitchSupervisorStepState(),
    )


class NeverShutdownWaiter:
    """A non-blocking test waiter; the patched runtime controls normal exit."""

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        return False


class BasicPitchWorkerEntrypointTests(unittest.TestCase):
    """Prove only validated dependency factories reach the runtime loop."""

    @patch("app.runtime.worker_entrypoint.run_basic_pitch_worker_runtime")
    @patch("app.runtime.worker_entrypoint._validated_work_directory")
    @patch("app.runtime.worker_entrypoint.create_boto3_basic_pitch_minio_client")
    @patch("app.runtime.worker_entrypoint.BasicPitchMinioSettings.from_environment")
    @patch("app.runtime.worker_entrypoint.PsycopgBasicPitchDatabase")
    @patch("app.runtime.worker_entrypoint.BasicPitchAMQPSettings.from_environment")
    def test_bootstrap_builds_restricted_dependencies_then_maps_clean_shutdown(
        self,
        amqp_from_environment,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        run_runtime,
    ) -> None:
        """No direct DSN, ambient AWS client, or arbitrary scratch path is accepted."""

        amqp = amqp_settings()
        database = MagicMock()
        minio = minio_settings()
        storage_client = MagicMock()
        scratch = Path("/worker-scratch")
        waiter = NeverShutdownWaiter()
        amqp_from_environment.return_value = amqp
        database_factory.return_value = database
        minio_from_environment.return_value = minio
        minio_client_factory.return_value = storage_client
        work_directory.return_value = scratch
        run_runtime.return_value = runtime_result(BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED)

        returned = run_basic_pitch_worker_entrypoint(
            shutdown_waiter=waiter,
            process_timeout_seconds=120,
            jitter_fraction=0.25,
        )

        self.assertIsInstance(returned, BasicPitchWorkerEntrypointResult)
        self.assertEqual(returned.exit_status, EXIT_STATUS_SUCCESS)
        amqp_from_environment.assert_called_once_with()
        database_factory.assert_called_once_with()
        minio_from_environment.assert_called_once_with()
        minio_client_factory.assert_called_once_with(minio)
        work_directory.assert_called_once_with()
        run_runtime.assert_called_once_with(
            amqp_settings=amqp,
            database=database,
            storage_client=storage_client,
            work_directory=scratch,
            shutdown_waiter=waiter,
            process_timeout_seconds=120,
            process_runner=None,
            jitter_fraction=0.25,
        )

    def test_runtime_exit_reason_maps_to_only_success_or_configuration_status(self) -> None:
        """The future executable can return an explicit portable integer without `sys.exit`."""

        self.assertEqual(
            exit_status_for_basic_pitch_worker_runtime(
                runtime_result(BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED)
            ),
            EXIT_STATUS_SUCCESS,
        )
        self.assertEqual(
            exit_status_for_basic_pitch_worker_runtime(
                runtime_result(BasicPitchWorkerExitReason.FATAL_CONFIGURATION)
            ),
            EXIT_STATUS_CONFIGURATION_ERROR,
        )

    def test_scratch_mount_must_exist_as_a_real_directory_and_is_never_created(self) -> None:
        """A missing `emptyDir` mount fails rather than falling back to image storage."""

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scratch = root / "worker-scratch"
            scratch.mkdir()
            self.assertEqual(_validated_work_directory(scratch), scratch.resolve())
            with self.assertRaises(BasicPitchWorkerEntrypointConfigurationError):
                _validated_work_directory(root / "missing-worker-scratch")

    @patch("app.runtime.worker_entrypoint.run_basic_pitch_worker_runtime")
    @patch("app.runtime.worker_entrypoint._validated_work_directory")
    @patch("app.runtime.worker_entrypoint.create_boto3_basic_pitch_minio_client")
    @patch("app.runtime.worker_entrypoint.BasicPitchMinioSettings.from_environment")
    @patch("app.runtime.worker_entrypoint.PsycopgBasicPitchDatabase")
    @patch("app.runtime.worker_entrypoint.BasicPitchAMQPSettings.from_environment")
    def test_runtime_fatal_result_maps_to_configuration_exit_status(
        self,
        amqp_from_environment,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        run_runtime,
    ) -> None:
        """A reviewed fatal worker condition is visible to Kubernetes as exit 78."""

        amqp_from_environment.return_value = amqp_settings()
        database_factory.return_value = MagicMock()
        minio_from_environment.return_value = minio_settings()
        minio_client_factory.return_value = MagicMock()
        work_directory.return_value = Path("/worker-scratch")
        run_runtime.return_value = runtime_result(BasicPitchWorkerExitReason.FATAL_CONFIGURATION)

        returned = run_basic_pitch_worker_entrypoint(shutdown_waiter=NeverShutdownWaiter())

        self.assertEqual(returned.exit_status, EXIT_STATUS_CONFIGURATION_ERROR)

    @patch("app.runtime.worker_entrypoint.run_basic_pitch_worker_runtime")
    @patch("app.runtime.worker_entrypoint.BasicPitchAMQPSettings.from_environment")
    def test_invalid_mounted_configuration_stops_before_any_client_or_runtime_creation(
        self, amqp_from_environment, run_runtime
    ) -> None:
        """A bad Secret/topology setting remains the original reviewed fatal error."""

        amqp_from_environment.side_effect = BasicPitchAMQPConfigurationError(
            "safe test configuration failure"
        )

        with self.assertRaises(BasicPitchAMQPConfigurationError):
            run_basic_pitch_worker_entrypoint(shutdown_waiter=NeverShutdownWaiter())

        run_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
