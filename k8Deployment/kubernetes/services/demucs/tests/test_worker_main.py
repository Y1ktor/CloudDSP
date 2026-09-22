"""Unit tests for the Demucs executable status/diagnostic wrapper.

Tests patch the bootstrap entrypoint and stderr.  They do not read Secrets,
install signals, open services, run FFprobe/Demucs, build an image, or
create/apply a Kubernetes resource.
"""

from __future__ import annotations

import unittest
from unittest.mock import call, patch

from app.amqp_connection import DemucsAMQPConfigurationError
from app.supervisor_loop import DemucsSupervisorLoopOutcome, DemucsSupervisorLoopResult
from app.supervisor_step import DemucsSupervisorStepState
from app.worker_entrypoint import (
    EXIT_STATUS_CONFIGURATION_ERROR,
    EXIT_STATUS_SUCCESS,
    DemucsWorkerEntrypointResult,
)
from app.worker_main import main


def clean_shutdown_result() -> DemucsWorkerEntrypointResult:
    """Return bootstrap evidence without constructing any actual dependency."""

    supervisor = DemucsSupervisorLoopResult(
        outcome=DemucsSupervisorLoopOutcome.SHUTDOWN_REQUESTED,
        final_state=DemucsSupervisorStepState(),
        completed_cycles=0,
    )
    return DemucsWorkerEntrypointResult(
        supervisor=supervisor,
        exit_status=EXIT_STATUS_SUCCESS,
    )


class DemucsWorkerMainTests(unittest.TestCase):
    """Prove the executable boundary preserves only reviewed status behavior."""

    @patch("app.worker_main.run_demucs_worker_entrypoint")
    def test_main_returns_the_bootstrap_clean_shutdown_status(self, run_entrypoint) -> None:
        """The wrapper does not reinterpret a normal entrypoint terminal result."""

        run_entrypoint.return_value = clean_shutdown_result()

        self.assertEqual(main(), EXIT_STATUS_SUCCESS)
        run_entrypoint.assert_called_once_with()

    @patch("app.worker_main.run_demucs_worker_entrypoint")
    def test_main_masks_known_bootstrap_configuration_detail_with_status_78(self, run_entrypoint) -> None:
        """A malformed mounted setting remains actionable without logging its text."""

        run_entrypoint.side_effect = DemucsAMQPConfigurationError(
            "private RabbitMQ service and secret context"
        )

        with patch("app.worker_main.sys.stderr") as standard_error:
            returned = main()

        self.assertEqual(returned, EXIT_STATUS_CONFIGURATION_ERROR)
        # ``print`` emits the content and newline as separate write calls.
        standard_error.write.assert_has_calls(
            [
                call("demucs worker has invalid or incomplete configuration."),
                call("\n"),
            ]
        )
        self.assertNotIn("private RabbitMQ service", str(standard_error.write.call_args_list))

    @patch("app.worker_main.run_demucs_worker_entrypoint")
    def test_unreviewed_failure_propagates_after_existing_entrypoint_cleanup(self, run_entrypoint) -> None:
        """A task/runtime failure cannot be misrepresented as clean configuration exit."""

        unexpected = RuntimeError("unexpected worker failure")
        run_entrypoint.side_effect = unexpected

        with self.assertRaises(RuntimeError) as raised:
            main()

        self.assertIs(raised.exception, unexpected)


if __name__ == "__main__":
    unittest.main()
