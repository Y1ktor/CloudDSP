"""Tests for the explicit generic dispatcher process entrypoint.

All process dependencies are mocked.  The tests do not read a real Secret,
open PostgreSQL/RabbitMQ, install real signal handlers, sleep, or run a Pod.
"""

from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from app.dispatch_dispatchable_once import dispatch_dispatchable_once
from app.dispatcher_generic_runtime import main
from app.dispatcher_runtime import DispatcherRuntimeConfigurationError, DispatcherRuntimeSettings


class DispatcherGenericRuntimeTests(unittest.TestCase):
    """Prove generic routing requires an explicit process entrypoint choice."""

    @patch("app.dispatcher_generic_runtime.run_dispatcher_forever")
    @patch("app.dispatcher_generic_runtime._install_shutdown_handlers")
    @patch("app.dispatcher_generic_runtime.Event")
    @patch("app.dispatcher_generic_runtime.PsycopgDispatcherDatabase")
    @patch("app.dispatcher_generic_runtime.DispatcherPublisherSettings.from_environment")
    @patch("app.dispatcher_generic_runtime.DispatcherRuntimeSettings.from_environment")
    def test_main_reuses_the_supervisor_but_injects_only_generic_dispatch(
        self,
        load_runtime_settings,
        load_publisher_settings,
        create_database,
        create_event,
        install_handlers,
        run_forever,
    ) -> None:
        """The Demucs-only runtime module is never modified by this entrypoint."""

        runtime_settings = DispatcherRuntimeSettings()
        publisher_settings = MagicMock()
        database = MagicMock()
        stop_event = MagicMock(spec=Event)
        load_runtime_settings.return_value = runtime_settings
        load_publisher_settings.return_value = publisher_settings
        create_database.return_value = database
        create_event.return_value = stop_event

        exit_code = main()

        self.assertEqual(exit_code, 0)
        install_handlers.assert_called_once_with(stop_event)
        run_forever.assert_called_once_with(
            database=database,
            publisher_settings=publisher_settings,
            runtime_settings=runtime_settings,
            stop_requested=stop_event.is_set,
            dispatch=dispatch_dispatchable_once,
        )

    @patch("app.dispatcher_generic_runtime.run_dispatcher_forever")
    @patch(
        "app.dispatcher_generic_runtime.DispatcherRuntimeSettings.from_environment",
        side_effect=DispatcherRuntimeConfigurationError("safe"),
    )
    def test_configuration_error_returns_nonzero_before_process_setup(
        self,
        _load_runtime_settings,
        run_forever,
    ) -> None:
        """Invalid non-secret timing settings do not begin a generic loop."""

        self.assertEqual(main(), 2)
        run_forever.assert_not_called()


if __name__ == "__main__":
    unittest.main()
