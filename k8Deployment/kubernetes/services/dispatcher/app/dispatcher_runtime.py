"""Bounded long-running supervisor for the PostgreSQL outbox dispatcher.

This module is the final process-level layer over the small dispatcher
boundaries that already exist:

* ``app.outbox_lease`` owns atomic PostgreSQL lease SQL;
* ``app.postgresql`` owns short commit-or-rollback cursor scopes;
* ``app.amqp_publisher`` owns one restricted confirming RabbitMQ publish; and
* ``app.dispatch_once`` composes exactly one claim/publish/complete attempt.

The supervisor simply repeats that *bounded* attempt.  It never keeps a
PostgreSQL transaction open while RabbitMQ is contacted, declares RabbitMQ
topology, consumes worker queues, exposes HTTP, creates Kubernetes Jobs, or
executes Demucs.  A later image and Deployment can use ``python -m
app.dispatcher_runtime`` as their one-process entrypoint.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from app.amqp_publisher import (
    DispatcherPublisherConfigurationError,
    DispatcherPublisherSettings,
)
from app.dispatch_once import (
    DEFAULT_DISPATCH_LEASE_SECONDS,
    DEFAULT_RETRY_AFTER_SECONDS,
    DispatchOnceOutcome,
    DispatchOnceResult,
    dispatch_once,
)
from app.outbox_lease import (
    MAX_LEASE_SECONDS,
    MAX_RETRY_DELAY_SECONDS,
    MIN_LEASE_SECONDS,
    MIN_RETRY_DELAY_SECONDS,
)
from app.postgresql import (
    DispatcherDatabaseConfigurationError,
    DispatcherDatabaseUnavailable,
    PsycopgDispatcherDatabase,
)


# The dispatcher has no broker ``basic_get`` polling loop: each idle check is
# an indexed PostgreSQL outbox lease query.  These modest, bounded pauses avoid
# an empty-outbox CPU/DB hot loop while allowing an incoming SIGTERM to take
# effect promptly.  The longer delay is reserved for an unavailable database,
# where another connection attempt immediately is unlikely to help.
DEFAULT_IDLE_SLEEP_SECONDS = 1
DEFAULT_DATABASE_RECOVERY_SLEEP_SECONDS = 5
MAX_IDLE_SLEEP_SECONDS = 10
MAX_DATABASE_RECOVERY_SLEEP_SECONDS = 60


class DispatcherRuntimeConfigurationError(RuntimeError):
    """Raise a safe category for invalid non-secret supervisor settings."""


def _bounded_positive_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse one bounded timing setting before it controls durable work.

    Environment variables are text even when a Deployment sources them from a
    ConfigMap.  Parsing them at start-up makes an accidental ``0`` or very long
    lease a visible configuration failure instead of a silent hot loop or a
    stranded outbox event.  The exception includes only an environment name
    and range, never a credential or message-specific value.
    """

    try:
        parsed = int(value)
    except ValueError as error:
        raise DispatcherRuntimeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise DispatcherRuntimeConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return parsed


@dataclass(frozen=True)
class DispatcherRuntimeSettings:
    """Non-secret timing policy for one dispatcher Pod process.

    ``lease_seconds`` and ``retry_after_seconds`` are supplied to every
    ``dispatch_once`` call.  They are deliberately independent:

    * a lease bounds recovery after an uncertain confirmation or stopped Pod;
    * retry-after delays a failure known not to have been confirmed by RabbitMQ;
    * idle sleep limits database polling when no row is due; and
    * database recovery sleep limits failed PostgreSQL connection attempts.

    Future Deployment configuration may set the same values through a
    ConfigMap.  These settings must never contain a password, URI, token,
    object key, event ID, or job ID.
    """

    lease_seconds: int = DEFAULT_DISPATCH_LEASE_SECONDS
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS
    idle_sleep_seconds: int = DEFAULT_IDLE_SLEEP_SECONDS
    database_recovery_sleep_seconds: int = DEFAULT_DATABASE_RECOVERY_SLEEP_SECONDS

    @classmethod
    def from_environment(cls) -> "DispatcherRuntimeSettings":
        """Load bounded non-secret timing controls from the process environment."""

        return cls(
            lease_seconds=_bounded_positive_integer(
                name="DISPATCHER_LEASE_SECONDS",
                value=os.environ.get("DISPATCHER_LEASE_SECONDS", str(DEFAULT_DISPATCH_LEASE_SECONDS)),
                minimum=MIN_LEASE_SECONDS,
                maximum=MAX_LEASE_SECONDS,
            ),
            retry_after_seconds=_bounded_positive_integer(
                name="DISPATCHER_RETRY_AFTER_SECONDS",
                value=os.environ.get("DISPATCHER_RETRY_AFTER_SECONDS", str(DEFAULT_RETRY_AFTER_SECONDS)),
                minimum=MIN_RETRY_DELAY_SECONDS,
                maximum=MAX_RETRY_DELAY_SECONDS,
            ),
            idle_sleep_seconds=_bounded_positive_integer(
                name="DISPATCHER_IDLE_SLEEP_SECONDS",
                value=os.environ.get("DISPATCHER_IDLE_SLEEP_SECONDS", str(DEFAULT_IDLE_SLEEP_SECONDS)),
                minimum=1,
                maximum=MAX_IDLE_SLEEP_SECONDS,
            ),
            database_recovery_sleep_seconds=_bounded_positive_integer(
                name="DISPATCHER_DATABASE_RECOVERY_SLEEP_SECONDS",
                value=os.environ.get(
                    "DISPATCHER_DATABASE_RECOVERY_SLEEP_SECONDS",
                    str(DEFAULT_DATABASE_RECOVERY_SLEEP_SECONDS),
                ),
                minimum=1,
                maximum=MAX_DATABASE_RECOVERY_SLEEP_SECONDS,
            ),
        )


# Injection points make the supervisor's timing and stop behavior unit-testable
# without a sleeping test, a real signal, PostgreSQL, RabbitMQ, or a Pod.
SleepFunction = Callable[[float], None]
StopRequested = Callable[[], bool]
DispatchFunction = Callable[..., DispatchOnceResult]
FailureNotifier = Callable[[], None]


def _default_database_failure_notifier() -> None:
    """Emit one stable message without printing driver/endpoint diagnostics."""

    print("dispatcher retrying after PostgreSQL outbox access was unavailable.", file=sys.stderr)


def run_dispatcher_forever(
    *,
    database: PsycopgDispatcherDatabase,
    publisher_settings: DispatcherPublisherSettings,
    runtime_settings: DispatcherRuntimeSettings,
    stop_requested: StopRequested,
    sleep_function: SleepFunction = time.sleep,
    database_failure_notifier: FailureNotifier = _default_database_failure_notifier,
    dispatch: DispatchFunction = dispatch_once,
) -> None:
    """Dispatch due outbox events until Kubernetes asks this process to stop.

    One iteration can claim and publish only one event.  Successful, stale, or
    terminal outcomes immediately start the next bounded iteration so a busy
    backlog drains without a needless per-message delay.  An empty outbox
    sleeps briefly.  A known RabbitMQ failure is already persisted by
    ``dispatch_once`` with ``available_at`` in the future, so the next poll
    naturally becomes idle rather than repeatedly publishing the same event.

    ``DispatcherDatabaseUnavailable`` is the sole recoverable exception here.
    It can occur before a claim or while recording a result; no unsafe
    acknowledgement is possible because this process consumes no AMQP message.
    The failed short transaction rolls back, then the bounded database pause
    prevents a connection hot loop.  Contract/schema/programming errors are
    intentionally allowed to end the process: Kubernetes should restart a Pod
    with a fixed image/configuration instead of hiding a deterministic bug in
    an infinite loop.
    """

    while not stop_requested():
        try:
            result = dispatch(
                database=database,
                publisher_settings=publisher_settings,
                lease_seconds=runtime_settings.lease_seconds,
                retry_after_seconds=runtime_settings.retry_after_seconds,
            )
        except DispatcherDatabaseUnavailable:
            # The database adapter intentionally discards the raw Psycopg
            # diagnostic.  Do not add it back to normal Pod logs here; host
            # names, driver details, and operating state do not help recover
            # this retryable category.
            if not stop_requested():
                database_failure_notifier()
                sleep_function(runtime_settings.database_recovery_sleep_seconds)
            continue

        if result.outcome is DispatchOnceOutcome.IDLE:
            # No claimable pending event or expired lease exists.  This pause
            # is merely polling efficiency; PostgreSQL remains authoritative
            # and a newly inserted outbox row persists until the next check.
            if not stop_requested():
                sleep_function(runtime_settings.idle_sleep_seconds)


def _install_shutdown_handlers(stop_event: Event) -> None:
    """Convert SIGTERM/SIGINT into cooperative dispatcher-loop shutdown.

    Kubernetes sends SIGTERM before the Pod's termination grace period.  A
    Python signal handler must stay tiny: it only flips an in-memory Event and
    never opens a database transaction or publishes a broker message.  After
    its current bounded attempt or short pause, the supervisor sees the flag
    and returns normally.  Any leased-but-uncompleted row is recovered by the
    existing lease-expiry SQL, which may safely duplicate-publish downstream.
    """

    def request_stop(_signal_number: int, _current_frame: Any) -> None:
        """Record shutdown without serializing sensitive runtime state."""

        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def main() -> int:
    """Run the dispatcher process; suitable for a later container entrypoint."""

    try:
        # Configuration faults are not transient database outages.  Returning
        # nonzero lets a future Deployment surface a bad ConfigMap/Secret as a
        # failing Pod rather than reconnecting forever with incorrect settings.
        runtime_settings = DispatcherRuntimeSettings.from_environment()
        publisher_settings = DispatcherPublisherSettings.from_environment()
        database = PsycopgDispatcherDatabase()
    except (
        DispatcherRuntimeConfigurationError,
        DispatcherPublisherConfigurationError,
        DispatcherDatabaseConfigurationError,
    ):
        print("dispatcher has invalid or incomplete configuration.", file=sys.stderr)
        return 2

    stop_event = Event()
    _install_shutdown_handlers(stop_event)
    run_dispatcher_forever(
        database=database,
        publisher_settings=publisher_settings,
        runtime_settings=runtime_settings,
        stop_requested=stop_event.is_set,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
