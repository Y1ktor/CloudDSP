"""Unit tests for the Demucs environment/bootstrap entrypoint composition.

All dependency factories, signal scope, AMQP session, and supervisor loop are
patched.  These tests do not read a real Secret, change test-process signal
handlers, open PostgreSQL/MinIO/RabbitMQ, run FFprobe/Demucs, build an image,
or create/apply a Kubernetes resource.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.artifacts.minio_client import (
    LOCAL_S3_REGION,
    LOCAL_UPLOADS_BUCKET,
    DemucsMinioConfigurationError,
    DemucsMinioSettings,
)
from app.runtime.shutdown_event import DemucsShutdownWaiter
from app.runtime.supervisor_action import DemucsSupervisorActionOutcome, DemucsSupervisorActionResult
from app.runtime.supervisor_backoff import (
    DemucsSupervisorAction,
    DemucsSupervisorBackoffState,
    DemucsSupervisorDecision,
    DemucsSupervisorEvent,
)
from app.runtime.supervisor_loop import DemucsSupervisorLoopOutcome, DemucsSupervisorLoopResult
from app.runtime.supervisor_once import DemucsSupervisorOnceResult
from app.runtime.supervisor_step import DemucsSupervisorStepResult, DemucsSupervisorStepState
from app.runtime.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    EXIT_STATUS_SUCCESS,
    DemucsWorkerEntrypointConfigurationError,
    DemucsWorkerEntrypointResult,
    _validated_work_directory,
    exit_status_for_demucs_supervisor,
    run_demucs_worker_entrypoint,
)


def minio_settings() -> DemucsMinioSettings:
    """Return safe synthetic MinIO settings for patched-factory assertions."""

    return DemucsMinioSettings(
        internal_endpoint="http://clouddsp-minio.clouddsp-data.svc:9000",
        uploads_bucket=LOCAL_UPLOADS_BUCKET,
        region_name=LOCAL_S3_REGION,
        addressing_style="path",
        access_key="unit-test-demucs-access-key",
        secret_key="unit-test-demucs-secret-key",
    )


def shutdown_result() -> DemucsSupervisorLoopResult:
    """Return the valid zero-cycle result for a signal observed before work."""

    return DemucsSupervisorLoopResult(
        outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
        final_state=DemucsSupervisorStepState(),
        completed_cycles=0,
    )


def fatal_result() -> DemucsSupervisorLoopResult:
    """Return one structurally valid terminal fatal supervisor result."""

    state = DemucsSupervisorStepState()
    decision = DemucsSupervisorDecision(
        action=DemucsSupervisorAction.EXIT_FATAL,
        delay_seconds=0.0,
        next_state=DemucsSupervisorBackoffState(),
    )
    step = DemucsSupervisorStepResult(
        event=DemucsSupervisorEvent.FATAL_CONFIGURATION,
        decision=decision,
        next_state=state,
    )
    cycle = DemucsSupervisorOnceResult(
        step=step,
        action_result=DemucsSupervisorActionResult(
            outcome=DemucsSupervisorActionOutcome.EXIT_FATAL,
            action=DemucsSupervisorAction.EXIT_FATAL,
        ),
        next_state=state,
    )
    return DemucsSupervisorLoopResult(
        outcome=DemucsSupervisorLoopOutcome.EXIT_FATAL,
        final_state=state,
        completed_cycles=1,
        final_cycle=cycle,
    )


class DemucsWorkerEntrypointTests(unittest.TestCase):
    """Prove only reviewed factories and scopes reach the persistent loop."""

    @patch("app.runtime.worker_entrypoint.run_demucs_session_supervisor_until_stop")
    @patch("app.runtime.worker_entrypoint.installed_demucs_shutdown_waiter")
    @patch("app.runtime.worker_entrypoint._validated_work_directory")
    @patch("app.runtime.worker_entrypoint.create_boto3_demucs_minio_client")
    @patch("app.runtime.worker_entrypoint.DemucsMinioSettings.from_environment")
    @patch("app.runtime.worker_entrypoint.PsycopgDemucsDatabase")
    def test_composes_restricted_dependencies_and_scopes_signal_then_amqp_lifecycle(
        self,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        installed_shutdown_waiter,
        run_supervisor,
    ) -> None:
        """The session-owning loop starts only after signal installation."""

        events: list[str] = []
        database = MagicMock()
        storage_client = MagicMock()
        waiter = DemucsShutdownWaiter()
        scratch = Path("/worker-scratch")
        minio = minio_settings()

        @contextmanager
        def shutdown_scope():
            events.append("shutdown-enter")
            yield waiter
            events.append("shutdown-exit")

        database_factory.return_value = database
        minio_from_environment.return_value = minio
        minio_client_factory.return_value = storage_client
        work_directory.return_value = scratch
        installed_shutdown_waiter.side_effect = shutdown_scope
        expected_supervisor = shutdown_result()

        def run_loop(*_args, **_kwargs):
            events.append("supervisor")
            return expected_supervisor

        run_supervisor.side_effect = run_loop

        returned = run_demucs_worker_entrypoint()

        self.assertIsInstance(returned, DemucsWorkerEntrypointResult)
        self.assertIs(returned.supervisor, expected_supervisor)
        self.assertEqual(returned.exit_status, EXIT_STATUS_SUCCESS)
        self.assertEqual(
            events,
            ["shutdown-enter", "supervisor", "shutdown-exit"],
        )
        database_factory.assert_called_once_with()
        minio_from_environment.assert_called_once_with()
        minio_client_factory.assert_called_once_with(minio)
        work_directory.assert_called_once_with()
        installed_shutdown_waiter.assert_called_once_with()
        run_supervisor.assert_called_once_with(
            initial_state=DemucsSupervisorStepState(),
            database=database,
            source_client=storage_client,
            artifact_client=storage_client,
            work_directory=scratch,
            shutdown_waiter=waiter,
        )

    @patch("app.runtime.worker_entrypoint.run_demucs_session_supervisor_until_stop")
    @patch("app.runtime.worker_entrypoint.installed_demucs_shutdown_waiter")
    @patch("app.runtime.worker_entrypoint._validated_work_directory")
    @patch("app.runtime.worker_entrypoint.create_boto3_demucs_minio_client")
    @patch("app.runtime.worker_entrypoint.DemucsMinioSettings.from_environment")
    @patch("app.runtime.worker_entrypoint.PsycopgDemucsDatabase")
    def test_fatal_loop_result_maps_to_configuration_status(
        self,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        installed_shutdown_waiter,
        run_supervisor,
    ) -> None:
        """A classified fatal outcome returns 78 after both scopes close."""

        database_factory.return_value = MagicMock()
        minio_from_environment.return_value = minio_settings()
        minio_client_factory.return_value = MagicMock()
        work_directory.return_value = Path("/worker-scratch")
        installed_shutdown_waiter.return_value.__enter__.return_value = DemucsShutdownWaiter()
        run_supervisor.return_value = fatal_result()

        returned = run_demucs_worker_entrypoint()

        self.assertEqual(returned.exit_status, EXIT_STATUS_CONFIGURATION_ERROR)
        self.assertEqual(
            exit_status_for_demucs_supervisor(returned.supervisor),
            EXIT_STATUS_CONFIGURATION_ERROR,
        )

    def test_fixed_scratch_mount_must_exist_as_a_real_directory_and_is_never_created(self) -> None:
        """A missing/symlinked mount fails rather than falling back to image storage."""

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scratch = root / "worker-scratch"
            scratch.mkdir()
            self.assertEqual(_validated_work_directory(scratch), scratch.resolve())

            with self.assertRaises(DemucsWorkerEntrypointConfigurationError):
                _validated_work_directory(root / "missing-worker-scratch")

            symlink = root / "scratch-link"
            symlink.symlink_to(scratch, target_is_directory=True)
            with self.assertRaises(DemucsWorkerEntrypointConfigurationError):
                _validated_work_directory(symlink)

    @patch("app.runtime.worker_entrypoint.run_demucs_session_supervisor_until_stop")
    @patch("app.runtime.worker_entrypoint.installed_demucs_shutdown_waiter")
    @patch("app.runtime.worker_entrypoint.DemucsMinioSettings.from_environment")
    @patch("app.runtime.worker_entrypoint.PsycopgDemucsDatabase")
    def test_static_minio_configuration_failure_stops_before_signal_or_broker_setup(
        self,
        database_factory,
        minio_from_environment,
        installed_shutdown_waiter,
        run_supervisor,
    ) -> None:
        """A malformed mounted Secret cannot progress into signal/broker runtime work."""

        minio_from_environment.side_effect = DemucsMinioConfigurationError(
            "safe synthetic configuration failure"
        )

        with self.assertRaises(DemucsMinioConfigurationError):
            run_demucs_worker_entrypoint()

        database_factory.assert_called_once_with()
        installed_shutdown_waiter.assert_not_called()
        run_supervisor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
