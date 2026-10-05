"""Unit tests for the ADTOF executable status/diagnostic wrapper.

Tests patch the bootstrap entrypoint and stderr.  They do not read Secrets,
install signals, open services, run ADTOF, build an image, or create/apply a
Kubernetes resource.
"""

from __future__ import annotations

import runpy
import unittest
from unittest.mock import call, patch

from app.messaging.amqp_connection import ADTOFAMQPConfigurationError
from app.runtime.supervisor_loop import ADTOFSupervisorLoopOutcome, ADTOFSupervisorLoopResult
from app.runtime.supervisor_step import ADTOFSupervisorStepState
from app.runtime.worker_entrypoint import EXIT_STATUS_CONFIGURATION_ERROR, EXIT_STATUS_SUCCESS, ADTOFWorkerEntrypointResult
from app.runtime.worker_main import main


def clean_shutdown_result() -> ADTOFWorkerEntrypointResult:
    """Return bootstrap evidence without constructing any actual dependency."""

    supervisor = ADTOFSupervisorLoopResult(
        outcome=ADTOFSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
        final_state=ADTOFSupervisorStepState(),
        completed_cycles=0,
    )
    return ADTOFWorkerEntrypointResult(
        supervisor=supervisor,
        exit_status=EXIT_STATUS_SUCCESS,
    )


class ADTOFWorkerMainTests(unittest.TestCase):
    """Prove the executable boundary preserves only reviewed status behavior."""

    @patch("app.runtime.worker_main.main", return_value=78)
    def test_public_module_launcher_preserves_the_runtime_exit_status(self, runtime_main) -> None:
        """The unchanged image/Helm command still reaches the runtime boundary."""

        with self.assertRaises(SystemExit) as raised:
            runpy.run_module("app.worker_main", run_name="__main__")
        self.assertEqual(raised.exception.code, 78)
        runtime_main.assert_called_once_with()

    @patch("app.runtime.worker_main.run_adtof_worker_entrypoint")
    def test_main_returns_the_bootstrap_clean_shutdown_status(self, run_entrypoint) -> None:
        """The wrapper does not reinterpret a normal entrypoint terminal result."""

        run_entrypoint.return_value = clean_shutdown_result()

        self.assertEqual(main(), EXIT_STATUS_SUCCESS)
        run_entrypoint.assert_called_once_with()

    @patch("app.runtime.worker_main.run_adtof_worker_entrypoint")
    def test_main_masks_known_bootstrap_configuration_detail_with_status_78(self, run_entrypoint) -> None:
        """A malformed mounted setting remains actionable without logging its text."""

        run_entrypoint.side_effect = ADTOFAMQPConfigurationError(
            "private RabbitMQ service and secret context"
        )

        with patch("app.runtime.worker_main.sys.stderr") as standard_error:
            returned = main()

        self.assertEqual(returned, EXIT_STATUS_CONFIGURATION_ERROR)
        # ``print`` emits the content and newline as separate write calls.
        standard_error.write.assert_has_calls(
            [
                call("adtof worker has invalid or incomplete configuration."),
                call("\n"),
            ]
        )
        self.assertNotIn("private RabbitMQ service", str(standard_error.write.call_args_list))

    @patch("app.runtime.worker_main.run_adtof_worker_entrypoint")
    def test_unreviewed_failure_propagates_after_existing_entrypoint_cleanup(self, run_entrypoint) -> None:
        """A task/runtime failure cannot be misrepresented as clean configuration exit."""

        unexpected = RuntimeError("unexpected worker failure")
        run_entrypoint.side_effect = unexpected

        with self.assertRaises(RuntimeError) as raised:
            main()

        self.assertIs(raised.exception, unexpected)


if __name__ == "__main__":
    unittest.main()
