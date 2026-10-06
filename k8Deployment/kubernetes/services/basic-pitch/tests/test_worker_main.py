"""Unit tests for Basic Pitch's executable signal and status wrapper.

The tests replace Python's global signal registration and the bootstrap
entrypoint.  They do not install real handlers, wait, open clients, read
Secrets, run Basic Pitch, build an image, or change Kubernetes resources.
"""

from __future__ import annotations

import signal
from threading import Event
import runpy
import unittest
from unittest.mock import call, patch

from app.messaging.amqp_connection import BasicPitchAMQPConfigurationError
from app.runtime.supervisor_step import BasicPitchSupervisorStepState
from app.runtime.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    EXIT_STATUS_SUCCESS,
    BasicPitchWorkerEntrypointResult,
)
from app.runtime.worker_main import (
    BasicPitchEventShutdownWaiter,
    install_basic_pitch_shutdown_handlers,
    main,
)
from app.runtime.worker_runtime import BasicPitchWorkerExitReason, BasicPitchWorkerRuntimeResult


def clean_shutdown_result() -> BasicPitchWorkerEntrypointResult:
    """Return compact bootstrap evidence without constructing real dependencies."""

    return BasicPitchWorkerEntrypointResult(
        runtime=BasicPitchWorkerRuntimeResult(
            reason=BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED,
            completed_steps=0,
            final_state=BasicPitchSupervisorStepState(),
        ),
        exit_status=EXIT_STATUS_SUCCESS,
    )


class BasicPitchWorkerMainTests(unittest.TestCase):
    """Prove signal conversion and process status stay narrowly bounded."""

    @patch("app.runtime.worker_main.main", return_value=78)
    def test_public_module_launcher_preserves_the_runtime_exit_status(self, runtime_main) -> None:
        """The unchanged image/Helm command still reaches the runtime boundary."""

        with self.assertRaises(SystemExit) as raised:
            runpy.run_module("app.worker_main", run_name="__main__")
        self.assertEqual(raised.exception.code, 78)
        runtime_main.assert_called_once_with()

    def test_event_waiter_reports_unsignalled_and_signalled_shutdown_without_sleeping(self) -> None:
        """The runtime receives the exact Event.wait boolean contract it expects."""

        shutdown_event = Event()
        waiter = BasicPitchEventShutdownWaiter(shutdown_event)

        self.assertFalse(waiter.wait_for_shutdown(0.0))
        shutdown_event.set()
        self.assertTrue(waiter.wait_for_shutdown(0.0))

    @patch("app.runtime.worker_main.signal.signal")
    def test_signal_handlers_set_only_the_shared_shutdown_event(self, register_signal) -> None:
        """SIGTERM/SIGINT callbacks do no work beyond requesting cooperative shutdown."""

        captured_handlers: dict[signal.Signals, object] = {}

        def capture_registration(signal_number: signal.Signals, handler: object) -> None:
            captured_handlers[signal_number] = handler

        register_signal.side_effect = capture_registration
        shutdown_event = Event()

        install_basic_pitch_shutdown_handlers(shutdown_event)

        self.assertEqual(register_signal.call_count, 2)
        self.assertIn(signal.SIGINT, captured_handlers)
        self.assertIn(signal.SIGTERM, captured_handlers)
        sigterm_handler = captured_handlers[signal.SIGTERM]
        self.assertTrue(callable(sigterm_handler))
        sigterm_handler(signal.SIGTERM, None)  # type: ignore[operator]
        self.assertTrue(shutdown_event.is_set())

    @patch("app.runtime.worker_main.run_basic_pitch_worker_entrypoint")
    @patch("app.runtime.worker_main.install_basic_pitch_shutdown_handlers")
    def test_main_installs_handlers_before_delegating_and_returns_bootstrap_status(
        self, install_handlers, run_entrypoint
    ) -> None:
        """The executable layer owns signals but does not reinterpret a clean result."""

        run_entrypoint.return_value = clean_shutdown_result()

        returned = main()

        self.assertEqual(returned, EXIT_STATUS_SUCCESS)
        install_handlers.assert_called_once()
        run_entrypoint.assert_called_once()
        waiter = run_entrypoint.call_args.kwargs["shutdown_waiter"]
        self.assertIsInstance(waiter, BasicPitchEventShutdownWaiter)
        self.assertFalse(waiter.wait_for_shutdown(0.0))

    @patch("app.runtime.worker_main.run_basic_pitch_worker_entrypoint")
    @patch("app.runtime.worker_main.install_basic_pitch_shutdown_handlers")
    def test_main_maps_safe_bootstrap_configuration_error_without_printing_its_detail(
        self, _install_handlers, run_entrypoint
    ) -> None:
        """A bad mounted setting is visible as status 78 without leaking its text."""

        run_entrypoint.side_effect = BasicPitchAMQPConfigurationError("not-for-container-logs")

        with patch("app.runtime.worker_main.sys.stderr") as standard_error:
            returned = main()

        self.assertEqual(returned, EXIT_STATUS_CONFIGURATION_ERROR)
        # `print` writes the stable diagnostic and its newline separately to a
        # file-like object. Assert the complete safe output while proving the
        # original exception detail never became a normal Pod log line.
        standard_error.write.assert_has_calls(
            [
                call("basic-pitch worker has invalid or incomplete configuration."),
                call("\n"),
            ]
        )
        self.assertNotIn("not-for-container-logs", str(standard_error.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
