"""Perform exactly the one ADTOF action selected by the fairness cadence.

``recovery_cadence.py`` owns the local alternating schedule, while the two
existing bounded compositions own the actual normal AMQP and delivery-free
recovery paths. This adapter joins them for one cycle only: it reads the
immutable next action, invokes exactly that branch, validates the compact
result by advancing the cadence state, and returns both facts together.

It deliberately is not a loop or a connection lifecycle. The caller keeps the
returned state, applies any supervisor wait/backoff decision separately, and
calls this function again later. A recovery-selected cycle receives the shared
AMQP channel argument but does not pass it anywhere, which makes the absence
of a recovery broker operation explicit and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.processing.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.runtime.claimed_task_success import ADTOFClaimedTaskSuccessStorageClient
from app.runtime.recovery_cadence import (
    ADTOFWorkerCadenceAction,
    ADTOFWorkerCadenceState,
    advance_after_adtof_normal_iteration,
    advance_after_adtof_recovery_iteration,
)
from app.runtime.recovery_execute_once import (
    ADTOFRecoveryIterationDatabase,
    ADTOFRecoveryIterationResult,
    recover_and_execute_adtof_once,
)
from app.runtime.receive_execute_once import (
    ADTOFWorkerIterationDatabase,
    ADTOFWorkerIterationResult,
    receive_and_execute_adtof_once,
)


class ADTOFWorkerCycleDatabase(
    ADTOFWorkerIterationDatabase,
    ADTOFRecoveryIterationDatabase,
    Protocol,
):
    """Restricted database surface shared by one selected worker action.

    Each child composition still owns its own short transaction boundaries.
    Combining their structural protocols here does not authorize one shared
    transaction around RabbitMQ, MinIO, CPU execution, or any following cycle.
    """


@dataclass(frozen=True)
class ADTOFWorkerCycleResult:
    """One completed cadence action and the only state valid for the next cycle.

    The mutually exclusive fields retain only lower layers' compact results.
    They deliberately exclude raw RabbitMQ deliveries, lease tokens, event
    payloads, storage responses, scratch paths, credentials, and clients.
    """

    action: ADTOFWorkerCadenceAction
    next_state: ADTOFWorkerCadenceState
    normal_iteration: ADTOFWorkerIterationResult | None = None
    recovery_iteration: ADTOFRecoveryIterationResult | None = None

    def __post_init__(self) -> None:
        """Ensure callers cannot report one branch while advancing the other."""

        if not isinstance(self.action, ADTOFWorkerCadenceAction):
            raise TypeError("ADTOF worker cycle action is invalid.")
        if not isinstance(self.next_state, ADTOFWorkerCadenceState):
            raise TypeError("ADTOF worker cycle next state is invalid.")

        if self.action is ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION:
            if not isinstance(self.normal_iteration, ADTOFWorkerIterationResult):
                raise ValueError("A normal ADTOF worker cycle requires a normal iteration result.")
            if self.recovery_iteration is not None:
                raise ValueError("A normal ADTOF worker cycle cannot include recovery evidence.")
            expected_next = ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN
        elif self.action is ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN:
            if not isinstance(self.recovery_iteration, ADTOFRecoveryIterationResult):
                raise ValueError("A recovery ADTOF worker cycle requires a recovery iteration result.")
            if self.normal_iteration is not None:
                raise ValueError("A recovery ADTOF worker cycle cannot include normal evidence.")
            expected_next = ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION
        else:  # Defensive guard for any future action vocabulary extension.
            raise ValueError("ADTOF worker cycle action is invalid.")

        if self.next_state.next_action is not expected_next:
            raise ValueError("ADTOF worker cycle next state does not follow its action.")


def run_one_adtof_worker_cycle(
    channel: Any,
    *,
    state: ADTOFWorkerCadenceState,
    database: ADTOFWorkerCycleDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFWorkerCycleResult:
    """Run exactly the cadence-selected normal or recovery action once.

    Operational errors from either child composition propagate unchanged. The
    cadence state is frozen, so failed branches cannot partially advance it;
    a later supervisor can classify the error and retry the same selected
    action after its bounded backoff. This function starts no loop, performs no
    wait, and owns no broker/connection lifecycle.
    """

    if not isinstance(state, ADTOFWorkerCadenceState):
        raise TypeError("state must be ADTOFWorkerCadenceState.")

    if state.next_action is ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION:
        normal_iteration = receive_and_execute_adtof_once(
            channel,
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        next_state = advance_after_adtof_normal_iteration(state, normal_iteration)
        return ADTOFWorkerCycleResult(
            action=ADTOFWorkerCadenceAction.RUN_NORMAL_ITERATION,
            next_state=next_state,
            normal_iteration=normal_iteration,
        )

    if state.next_action is ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN:
        # Do not pass `channel`: recovery has no raw delivery and therefore no
        # acknowledgement/rejection/publish action to make against RabbitMQ.
        recovery_iteration = recover_and_execute_adtof_once(
            database=database,
            storage_client=storage_client,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        next_state = advance_after_adtof_recovery_iteration(state, recovery_iteration)
        return ADTOFWorkerCycleResult(
            action=ADTOFWorkerCadenceAction.RUN_RECOVERY_SCAN,
            next_state=next_state,
            recovery_iteration=recovery_iteration,
        )

    # Frozen state validation blocks this in ordinary use. Retaining a narrow
    # defensive error keeps a bypassed dataclass construction from selecting an
    # unreviewed future action while still avoiding any external work.
    raise TypeError("ADTOF worker cadence action is invalid.")
