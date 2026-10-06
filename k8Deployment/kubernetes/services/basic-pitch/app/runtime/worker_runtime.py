"""Run the Basic Pitch worker loop with explicit broker and shutdown lifecycle.

This module is the first actual loop composition for the local Basic Pitch
worker.  It deliberately receives already-constructed restricted dependencies:
the approved AMQP settings, Basic Pitch PostgreSQL adapter, MinIO client, and
shutdown-aware waiter.  It opens one RabbitMQ connection for each healthy
broker session, configures one prefetch-one/passive-checked channel, then
repeatedly combines the existing supervisor step and action boundaries until
shutdown or fatal configuration.

The scope is intentionally narrow:

* it does **not** read environment variables or construct database/MinIO
  clients (a later bootstrap/entrypoint task owns that);
* it reconnects only after a supervisor-classified retryable failure, closing
  the old connection before its bounded backoff so RabbitMQ can redeliver any
  unacknowledged delivery; and
* it does **not** install signal handlers, call ``sys.exit``, expose health
  endpoints, modify Kubernetes resources, or make a deployment change.

The loop returns a compact exit reason after closing its one broker connection.
Unexpected and task-specific exceptions propagate after that same cleanup, so
the outer entrypoint can surface them rather than converting incomplete work
into a false successful exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.messaging.amqp_channel import configure_basic_pitch_rabbitmq_channel
from app.messaging.amqp_connection import BasicPitchAMQPSettings, open_basic_pitch_rabbitmq_connection
from app.processing.basic_pitch_process import BasicPitchProcessRunner, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS
from app.runtime.basic_pitch_task_execution import BasicPitchTaskExecutionStorageClient
from app.runtime.supervisor_action import (
    BasicPitchShutdownWaiter,
    BasicPitchSupervisorActionOutcome,
    apply_basic_pitch_supervisor_decision,
)
from app.runtime.supervisor_backoff import BasicPitchSupervisorEvent
from app.runtime.supervisor_step import (
    BasicPitchSupervisorStepResult,
    BasicPitchSupervisorStepState,
    run_one_basic_pitch_supervisor_step,
)
from app.runtime.work_source_iteration import BasicPitchFairWorkIterationDatabase


class BasicPitchWorkerAMQPConnection(Protocol):
    """The minimal connection lifecycle surface owned by this runtime loop."""

    def channel(self) -> Any:
        """Create the one Pika-shaped channel configured by the reviewed helper."""

    def close(self) -> object:
        """Close the one connection when the loop exits for any reason."""


class BasicPitchWorkerExitReason(StrEnum):
    """The two normal reasons this loop can return to its outer entrypoint."""

    SHUTDOWN_REQUESTED = "shutdown_requested"
    FATAL_CONFIGURATION = "fatal_configuration"


@dataclass(frozen=True)
class BasicPitchWorkerRuntimeResult:
    """Compact normal exit evidence after the runtime has closed its connection."""

    reason: BasicPitchWorkerExitReason
    completed_steps: int
    final_state: BasicPitchSupervisorStepState

    def __post_init__(self) -> None:
        """Keep result facts safe for an outer process/metrics boundary."""

        if not isinstance(self.reason, BasicPitchWorkerExitReason):
            raise TypeError("Basic Pitch worker exit reason is invalid.")
        if type(self.completed_steps) is not int or self.completed_steps < 0:
            raise TypeError("Basic Pitch worker completed step count is invalid.")
        if not isinstance(self.final_state, BasicPitchSupervisorStepState):
            raise TypeError("Basic Pitch worker final supervisor state is invalid.")


def _shutdown_requested(waiter: BasicPitchShutdownWaiter) -> bool:
    """Check shutdown without blocking so a loop never starts fresh work after it."""

    requested = waiter.wait_for_shutdown(0.0)
    if type(requested) is not bool:
        raise TypeError("Basic Pitch shutdown waiter must return a boolean.")
    return requested


def _close_connection(connection: BasicPitchWorkerAMQPConnection) -> None:
    """Close exactly the connection this loop opened; do not hide close failures."""

    if not callable(getattr(connection, "close", None)):
        raise TypeError("Basic Pitch RabbitMQ connection must provide close.")
    connection.close()


def run_basic_pitch_worker_runtime(
    *,
    amqp_settings: BasicPitchAMQPSettings,
    database: BasicPitchFairWorkIterationDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    work_directory: Path,
    shutdown_waiter: BasicPitchShutdownWaiter,
    initial_state: BasicPitchSupervisorStepState = BasicPitchSupervisorStepState(),
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
    jitter_fraction: float = 0.0,
) -> BasicPitchWorkerRuntimeResult:
    """Run broker sessions until normal exit, rebuilding only after safe retries.

    The function checks the injected shutdown waiter before opening a socket,
    after channel setup, and before each new supervisor step. Therefore a
    termination request can prevent both initial and subsequent work attempts.
    Normal supervisor-action outcomes choose whether to continue, exit cleanly
    after an interrupted wait, or return fatal configuration.

    A retryable supervisor step is special: it may have received a
    ``prefetch=1`` manual-ack delivery and then lost PostgreSQL/RabbitMQ
    availability before an acknowledgement decision. The runtime closes that
    connection *before* waiting. RabbitMQ then releases the channel's
    unacknowledged delivery for at-least-once redelivery, and a fresh
    connection/channel is created only after the bounded backoff expires.
    This prevents the old channel's prefetch limit from holding a delivery
    indefinitely. The connection is otherwise closed in ``finally`` on every
    post-open normal or exceptional path.

    Connection open/channel creation/configuration faults occur outside a
    supervisor step and still propagate after cleanup; they have no known
    completed delivery decision to recover here. This loop does not call
    ``sleep``, install a signal handler, construct clients from environment
    variables, or call ``sys.exit``.
    """

    if not isinstance(amqp_settings, BasicPitchAMQPSettings):
        raise TypeError("amqp_settings must be BasicPitchAMQPSettings.")
    if not isinstance(initial_state, BasicPitchSupervisorStepState):
        raise TypeError("initial_state must be BasicPitchSupervisorStepState.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    if not callable(getattr(shutdown_waiter, "wait_for_shutdown", None)):
        raise TypeError("shutdown_waiter must provide wait_for_shutdown.")

    state = initial_state
    completed_steps = 0
    while True:
        # Do not open an initial or replacement broker socket after Kubernetes
        # termination was requested. This check is intentionally outside the
        # per-session scope as well as inside it below.
        if _shutdown_requested(shutdown_waiter):
            return BasicPitchWorkerRuntimeResult(
                reason=BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED,
                completed_steps=completed_steps,
                final_state=state,
            )

        connection = open_basic_pitch_rabbitmq_connection(amqp_settings)
        if not callable(getattr(connection, "channel", None)):
            # A malformed factory result is never treated as a channel or
            # normal worker exit. Close it before surfacing the programmer or
            # dependency failure, exactly as for a normal session failure.
            try:
                _close_connection(connection)
            finally:
                raise TypeError("Basic Pitch RabbitMQ connection must provide channel.")

        # A retryable step closes the session explicitly before it waits, so
        # `finally` must not close the same Pika connection a second time.
        connection_closed_for_retry = False
        try:
            channel = connection.channel()
            configure_basic_pitch_rabbitmq_channel(channel, settings=amqp_settings)

            while True:
                # This zero-time check is distinct from the action wait: an
                # immediate-progress decision otherwise starts a next task before
                # noticing shutdown because it intentionally has no delay.
                if _shutdown_requested(shutdown_waiter):
                    return BasicPitchWorkerRuntimeResult(
                        reason=BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED,
                        completed_steps=completed_steps,
                        final_state=state,
                    )

                step = run_one_basic_pitch_supervisor_step(
                    channel,
                    state=state,
                    database=database,
                    storage_client=storage_client,
                    work_directory=work_directory,
                    process_timeout_seconds=process_timeout_seconds,
                    process_runner=process_runner,
                    jitter_fraction=jitter_fraction,
                )
                if not isinstance(step, BasicPitchSupervisorStepResult):
                    raise TypeError("Basic Pitch supervisor step returned an invalid result.")
                completed_steps += 1

                retryable_failure = step.event is BasicPitchSupervisorEvent.RETRYABLE_FAILURE
                if retryable_failure:
                    # Do not wait while an unacknowledged delivery remains on a
                    # `prefetch=1` channel. Closing without an ack/nack leaves
                    # RabbitMQ as the delivery authority: it requeues the
                    # message for a later at-least-once redelivery instead of
                    # losing it or holding this worker session permanently.
                    _close_connection(connection)
                    connection_closed_for_retry = True

                action_result = apply_basic_pitch_supervisor_decision(
                    step.decision,
                    shutdown_waiter=shutdown_waiter,
                )
                if action_result.outcome is BasicPitchSupervisorActionOutcome.CONTINUE:
                    state = step.next_state
                    if retryable_failure:
                        # Leave this closed session only after the backoff. The
                        # outer loop rechecks SIGTERM then opens/configures a
                        # fresh private connection and channel.
                        break
                    continue
                if action_result.outcome is BasicPitchSupervisorActionOutcome.SHUTDOWN_REQUESTED:
                    return BasicPitchWorkerRuntimeResult(
                        reason=BasicPitchWorkerExitReason.SHUTDOWN_REQUESTED,
                        completed_steps=completed_steps,
                        final_state=step.next_state,
                    )
                if action_result.outcome is BasicPitchSupervisorActionOutcome.EXIT_FATAL:
                    return BasicPitchWorkerRuntimeResult(
                        reason=BasicPitchWorkerExitReason.FATAL_CONFIGURATION,
                        completed_steps=completed_steps,
                        final_state=step.next_state,
                    )
                raise RuntimeError("Basic Pitch supervisor action returned an invalid outcome.")
        finally:
            if not connection_closed_for_retry:
                _close_connection(connection)
