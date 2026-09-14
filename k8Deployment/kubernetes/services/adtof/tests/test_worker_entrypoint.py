"""Unit tests for the ADTOF environment/bootstrap entrypoint composition.

All dependency factories, signal scope, AMQP session, and supervisor loop are
patched.  These tests do not read a real Secret, change test-process signal
handlers, open PostgreSQL/MinIO/RabbitMQ, execute ADTOF, build an image, or
create/apply a Kubernetes resource.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from app.minio_client import (
    LOCAL_ARTIFACTS_BUCKET,
    LOCAL_MINIO_INTERNAL_ENDPOINT,
    LOCAL_S3_ADDRESSING_STYLE,
    LOCAL_S3_REGION,
    ADTOFMinioConfigurationError,
    ADTOFMinioSettings,
)
from app.shutdown_event import ADTOFShutdownWaiter
from app.supervisor_action import ADTOFSupervisorActionOutcome, ADTOFSupervisorActionResult
from app.supervisor_backoff import ADTOFSupervisorAction, ADTOFSupervisorBackoffState, ADTOFSupervisorDecision, ADTOFSupervisorEvent
from app.supervisor_loop import ADTOFSupervisorLoopOutcome, ADTOFSupervisorLoopResult
from app.supervisor_once import ADTOFSupervisorOnceResult
from app.supervisor_step import ADTOFSupervisorStepResult, ADTOFSupervisorStepState
from app.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    EXIT_STATUS_SUCCESS,
    ADTOFWorkerEntrypointConfigurationError,
    ADTOFWorkerEntrypointResult,
    _validated_work_directory,
    exit_status_for_adtof_supervisor,
    run_adtof_worker_entrypoint,
)


def minio_settings() -> ADTOFMinioSettings:
    """Return safe synthetic MinIO settings for patched-factory assertions."""

    return ADTOFMinioSettings(
        internal_endpoint=LOCAL_MINIO_INTERNAL_ENDPOINT,
        artifacts_bucket=LOCAL_ARTIFACTS_BUCKET,
        region_name=LOCAL_S3_REGION,
        addressing_style=LOCAL_S3_ADDRESSING_STYLE,
        access_key="unit-test-adtof-access-key",
        secret_key="unit-test-adtof-secret-key",
    )


def shutdown_result() -> ADTOFSupervisorLoopResult:
    """Return the valid zero-cycle result for a signal observed before work."""

    return ADTOFSupervisorLoopResult(
        outcome=ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
        final_state=ADTOFSupervisorStepState(),
        completed_cycles=0,
    )


def fatal_result() -> ADTOFSupervisorLoopResult:
    """Return one structurally valid terminal fatal supervisor result."""

    state = ADTOFSupervisorStepState()
    decision = ADTOFSupervisorDecision(
        action=ADTOFSupervisorAction.EXIT_FATAL,
        delay_seconds=0.0,
        next_state=ADTOFSupervisorBackoffState(),
    )
    step = ADTOFSupervisorStepResult(
        event=ADTOFSupervisorEvent.FATAL_CONFIGURATION,
        decision=decision,
        next_state=state,
    )
    cycle = ADTOFSupervisorOnceResult(
        step=step,
        action_result=ADTOFSupervisorActionResult(
            outcome=ADTOFSupervisorActionOutcome.EXIT_FATAL,
            action=ADTOFSupervisorAction.EXIT_FATAL,
        ),
        next_state=state,
    )
    return ADTOFSupervisorLoopResult(
        outcome=ADTOFSupervisorLoopOutcome.EXIT_FATAL,
        final_state=state,
        completed_cycles=1,
        final_cycle=cycle,
    )


class ADTOFWorkerEntrypointTests(unittest.TestCase):
    """Prove only reviewed factories and scopes reach the persistent loop."""

    @patch("app.worker_entrypoint.run_adtof_supervisor_until_stop")
    @patch("app.worker_entrypoint.opened_adtof_rabbitmq_session")
    @patch("app.worker_entrypoint.installed_adtof_shutdown_waiter")
    @patch("app.worker_entrypoint._validated_work_directory")
    @patch("app.worker_entrypoint.create_boto3_adtof_minio_client")
    @patch("app.worker_entrypoint.ADTOFMinioSettings.from_environment")
    @patch("app.worker_entrypoint.PsycopgADTOFDatabase")
    def test_composes_restricted_dependencies_and_scopes_signal_then_amqp_lifecycle(
        self,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        installed_shutdown_waiter,
        opened_session,
        run_supervisor,
    ) -> None:
        """One prepared channel reaches one loop after signal installation only."""

        events: list[str] = []
        database = MagicMock()
        storage_client = MagicMock()
        channel = MagicMock()
        waiter = ADTOFShutdownWaiter()
        scratch = Path("/worker-scratch")
        minio = minio_settings()

        @contextmanager
        def shutdown_scope():
            events.append("shutdown-enter")
            yield waiter
            events.append("shutdown-exit")

        @contextmanager
        def amqp_scope():
            events.append("amqp-enter")
            yield channel
            events.append("amqp-exit")

        database_factory.return_value = database
        minio_from_environment.return_value = minio
        minio_client_factory.return_value = storage_client
        work_directory.return_value = scratch
        installed_shutdown_waiter.side_effect = shutdown_scope
        opened_session.side_effect = amqp_scope

        expected_supervisor = shutdown_result()

        def run_loop(*_args, **_kwargs):
            events.append("supervisor")
            return expected_supervisor

        run_supervisor.side_effect = run_loop

        returned = run_adtof_worker_entrypoint(
            process_timeout_seconds=120,
            jitter_fraction=0.25,
        )

        self.assertIsInstance(returned, ADTOFWorkerEntrypointResult)
        self.assertIs(returned.supervisor, expected_supervisor)
        self.assertEqual(returned.exit_status, EXIT_STATUS_SUCCESS)
        self.assertEqual(
            events,
            ["shutdown-enter", "amqp-enter", "supervisor", "amqp-exit", "shutdown-exit"],
        )
        database_factory.assert_called_once_with()
        minio_from_environment.assert_called_once_with()
        minio_client_factory.assert_called_once_with(minio)
        work_directory.assert_called_once_with()
        installed_shutdown_waiter.assert_called_once_with()
        opened_session.assert_called_once_with()
        run_supervisor.assert_called_once_with(
            channel,
            initial_state=ADTOFSupervisorStepState(),
            database=database,
            storage_client=storage_client,
            work_directory=scratch,
            shutdown_waiter=waiter,
            process_timeout_seconds=120,
            process_runner=None,
            jitter_fraction=0.25,
        )

    @patch("app.worker_entrypoint.run_adtof_supervisor_until_stop")
    @patch("app.worker_entrypoint.opened_adtof_rabbitmq_session")
    @patch("app.worker_entrypoint.installed_adtof_shutdown_waiter")
    @patch("app.worker_entrypoint._validated_work_directory")
    @patch("app.worker_entrypoint.create_boto3_adtof_minio_client")
    @patch("app.worker_entrypoint.ADTOFMinioSettings.from_environment")
    @patch("app.worker_entrypoint.PsycopgADTOFDatabase")
    def test_fatal_loop_result_maps_to_configuration_status(
        self,
        database_factory,
        minio_from_environment,
        minio_client_factory,
        work_directory,
        installed_shutdown_waiter,
        opened_session,
        run_supervisor,
    ) -> None:
        """A classified fatal outcome returns 78 after both scopes close."""

        database_factory.return_value = MagicMock()
        minio_from_environment.return_value = minio_settings()
        minio_client_factory.return_value = MagicMock()
        work_directory.return_value = Path("/worker-scratch")
        installed_shutdown_waiter.return_value.__enter__.return_value = ADTOFShutdownWaiter()
        opened_session.return_value.__enter__.return_value = MagicMock()
        run_supervisor.return_value = fatal_result()

        returned = run_adtof_worker_entrypoint()

        self.assertEqual(returned.exit_status, EXIT_STATUS_CONFIGURATION_ERROR)
        self.assertEqual(
            exit_status_for_adtof_supervisor(returned.supervisor),
            EXIT_STATUS_CONFIGURATION_ERROR,
        )

    def test_fixed_scratch_mount_must_exist_as_a_real_directory_and_is_never_created(self) -> None:
        """A missing/symlinked mount fails rather than falling back to image storage."""

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scratch = root / "worker-scratch"
            scratch.mkdir()
            self.assertEqual(_validated_work_directory(scratch), scratch.resolve())

            with self.assertRaises(ADTOFWorkerEntrypointConfigurationError):
                _validated_work_directory(root / "missing-worker-scratch")

            symlink = root / "scratch-link"
            symlink.symlink_to(scratch, target_is_directory=True)
            with self.assertRaises(ADTOFWorkerEntrypointConfigurationError):
                _validated_work_directory(symlink)

    @patch("app.worker_entrypoint.opened_adtof_rabbitmq_session")
    @patch("app.worker_entrypoint.installed_adtof_shutdown_waiter")
    @patch("app.worker_entrypoint.ADTOFMinioSettings.from_environment")
    @patch("app.worker_entrypoint.PsycopgADTOFDatabase")
    def test_static_minio_configuration_failure_stops_before_signal_or_broker_setup(
        self,
        database_factory,
        minio_from_environment,
        installed_shutdown_waiter,
        opened_session,
    ) -> None:
        """A malformed mounted Secret cannot progress into signal/broker runtime work."""

        minio_from_environment.side_effect = ADTOFMinioConfigurationError(
            "safe synthetic configuration failure"
        )

        with self.assertRaises(ADTOFMinioConfigurationError):
            run_adtof_worker_entrypoint()

        database_factory.assert_called_once_with()
        installed_shutdown_waiter.assert_not_called()
        opened_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
