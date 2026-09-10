"""Unit tests for the dispatcher's bounded long-running supervisor.

The supervisor receives an injected one-attempt dispatcher, clock, and stop
flag.  These tests therefore do not sleep, install OS signal handlers, read a
Secret, open PostgreSQL/RabbitMQ, publish a message, or run a Kubernetes Pod.
They make its recovery and idle-poll decisions reviewable before an image or
Deployment exists.
"""

from __future__ import annotations

import os
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from app.amqp_publisher import DispatcherPublisherSettings
from app.dispatch_once import DispatchOnceOutcome, DispatchOnceResult
from app.dispatcher_runtime import (
    DispatcherRuntimeConfigurationError,
    DispatcherRuntimeSettings,
    _install_shutdown_handlers,
    run_dispatcher_forever,
)
from app.postgresql import DispatcherDatabaseUnavailable


def publisher_settings() -> DispatcherPublisherSettings:
    """Return non-secret fake AMQP settings for one isolated loop test."""

    return DispatcherPublisherSettings(
        host="rabbitmq.test",
        port=5672,
        username="clouddsp-dispatcher",
        password="not-a-real-password",
    )


class DispatcherRuntimeSettingsTests(unittest.TestCase):
    """Prove timing controls are bounded before dispatcher work can begin."""

    @patch.dict(
        os.environ,
        {
            "DISPATCHER_LEASE_SECONDS": "45",
            "DISPATCHER_RETRY_AFTER_SECONDS": "30",
            "DISPATCHER_IDLE_SLEEP_SECONDS": "2",
            "DISPATCHER_DATABASE_RECOVERY_SLEEP_SECONDS": "8",
        },
        clear=True,
    )
    def test_loads_explicit_non_secret_timing_values(self) -> None:
        """A future ConfigMap can change waits without changing event bodies."""

        loaded = DispatcherRuntimeSettings.from_environment()

        self.assertEqual(loaded.lease_seconds, 45)
        self.assertEqual(loaded.retry_after_seconds, 30)
        self.assertEqual(loaded.idle_sleep_seconds, 2)
        self.assertEqual(loaded.database_recovery_sleep_seconds, 8)

    @patch.dict(os.environ, {"DISPATCHER_LEASE_SECONDS": "0"}, clear=True)
    def test_rejects_a_zero_lease_before_any_outbox_claim(self) -> None:
        """A zero lease would allow another replica to take work immediately."""

        with self.assertRaises(DispatcherRuntimeConfigurationError):
            DispatcherRuntimeSettings.from_environment()


class DispatcherSupervisorTests(unittest.TestCase):
    """Prove outcome-driven waiting and safe temporary database recovery."""

    def setUp(self) -> None:
        """Prepare shared non-network dependencies for each loop test."""

        self.database = MagicMock()
        self.settings = DispatcherRuntimeSettings(
            lease_seconds=30,
            retry_after_seconds=30,
            idle_sleep_seconds=2,
            database_recovery_sleep_seconds=7,
        )

    def test_idle_outbox_waits_once_then_stops(self) -> None:
        """An empty outbox pauses instead of issuing a tight SELECT loop."""

        stopped = {"value": False}
        sleep = MagicMock(side_effect=lambda _seconds: stopped.__setitem__("value", True))
        dispatch = MagicMock(return_value=DispatchOnceResult(DispatchOnceOutcome.IDLE))

        run_dispatcher_forever(
            database=self.database,
            publisher_settings=publisher_settings(),
            runtime_settings=self.settings,
            stop_requested=lambda: stopped["value"],
            sleep_function=sleep,
            dispatch=dispatch,
        )

        sleep.assert_called_once_with(2)
        dispatch.assert_called_once_with(
            database=self.database,
            publisher_settings=publisher_settings(),
            lease_seconds=30,
            retry_after_seconds=30,
        )

    def test_published_work_immediately_attempts_next_event_before_idling(self) -> None:
        """A backlog drains without an artificial delay after every event."""

        stopped = {"value": False}
        sleep = MagicMock(side_effect=lambda _seconds: stopped.__setitem__("value", True))
        dispatch = MagicMock(
            side_effect=(
                DispatchOnceResult(DispatchOnceOutcome.PUBLISHED),
                DispatchOnceResult(DispatchOnceOutcome.IDLE),
            )
        )

        run_dispatcher_forever(
            database=self.database,
            publisher_settings=publisher_settings(),
            runtime_settings=self.settings,
            stop_requested=lambda: stopped["value"],
            sleep_function=sleep,
            dispatch=dispatch,
        )

        self.assertEqual(dispatch.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_temporary_database_outage_notifies_waits_and_then_stops(self) -> None:
        """Only the database-safe category gets a bounded recovery retry."""

        stopped = {"value": False}
        sleep = MagicMock(side_effect=lambda _seconds: stopped.__setitem__("value", True))
        notifier = MagicMock()
        dispatch = MagicMock(side_effect=DispatcherDatabaseUnavailable("safe test category"))

        run_dispatcher_forever(
            database=self.database,
            publisher_settings=publisher_settings(),
            runtime_settings=self.settings,
            stop_requested=lambda: stopped["value"],
            sleep_function=sleep,
            database_failure_notifier=notifier,
            dispatch=dispatch,
        )

        notifier.assert_called_once_with()
        sleep.assert_called_once_with(7)
        dispatch.assert_called_once_with(
            database=self.database,
            publisher_settings=publisher_settings(),
            lease_seconds=30,
            retry_after_seconds=30,
        )

    def test_non_database_programming_error_is_not_hidden_in_a_retry_loop(self) -> None:
        """A deterministic bug must fail the Pod instead of spinning forever."""

        with self.assertRaisesRegex(RuntimeError, "test dispatcher bug"):
            run_dispatcher_forever(
                database=self.database,
                publisher_settings=publisher_settings(),
                runtime_settings=self.settings,
                stop_requested=lambda: False,
                dispatch=MagicMock(side_effect=RuntimeError("test dispatcher bug")),
            )

    @patch("app.dispatcher_runtime.signal.signal")
    def test_shutdown_handlers_only_mark_the_stop_event(self, register_signal) -> None:
        """A SIGTERM handler performs no database or RabbitMQ operation itself."""

        stop_event = Event()

        _install_shutdown_handlers(stop_event)

        self.assertEqual(register_signal.call_count, 2)
        sigterm_handler = register_signal.call_args_list[1].args[1]
        sigterm_handler(15, None)
        self.assertTrue(stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
