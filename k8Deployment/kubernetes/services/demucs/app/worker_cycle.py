"""Perform exactly the one Demucs action selected by recovery cadence.

``recovery_cadence.py`` owns the local alternating schedule; the two existing
bounded compositions own normal AMQP work and delivery-free recovery. This
adapter joins them for one cycle only: it reads immutable cadence state, runs
exactly the selected branch, validates/advances state, and returns both facts.

It is deliberately not a worker loop or connection lifecycle. The caller
retains the returned state, separately chooses any supervisor wait/backoff, and
calls this function again later. A recovery-selected cycle accepts the shared
channel argument for a stable caller shape, but does not pass it anywhere;
recovery has no raw delivery to acknowledge, reject, or publish.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.receive_execute_once import (
    DemucsWorkerIterationDatabase,
    DemucsWorkerIterationResult,
    receive_and_execute_demucs_once,
)
from app.recovery_cadence import (
    DemucsWorkerCadenceAction,
    DemucsWorkerCadenceState,
    advance_after_demucs_normal_iteration,
    advance_after_demucs_recovery_iteration,
)
from app.recovery_execute_once import (
    DemucsRecoveryIterationDatabase,
    DemucsRecoveryIterationResult,
    recover_and_execute_demucs_once,
)
from app.running_failure_transition import DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS
from app.source_preflight import DemucsSourcePreflightClient


class DemucsWorkerCycleDatabase(
    DemucsWorkerIterationDatabase,
    DemucsRecoveryIterationDatabase,
    Protocol,
):
    """Restricted database surface shared by one selected worker action.

    The child compositions retain their independent short transaction scopes.
    Combining structural protocols here never authorizes one transaction across
    RabbitMQ, private storage, FFprobe, inference, artifact upload, or a later
    cycle.
    """


@dataclass(frozen=True)
class DemucsWorkerCycleResult:
    """One completed cadence action and the sole valid state for the next cycle.

    The mutually exclusive fields hold only lower layers' compact durable-safe
    results. They exclude broker deliveries, lease tokens, event payloads,
    source/object coordinates, scratch paths, credentials, clients, and
    exceptions.
    """

    action: DemucsWorkerCadenceAction
    next_state: DemucsWorkerCadenceState
    normal_iteration: DemucsWorkerIterationResult | None = None
    recovery_iteration: DemucsRecoveryIterationResult | None = None

    def __post_init__(self) -> None:
        """Prevent one branch from being reported as another or skipping cadence."""

        if not isinstance(self.action, DemucsWorkerCadenceAction):
            raise TypeError("Demucs worker cycle action is invalid.")
        if not isinstance(self.next_state, DemucsWorkerCadenceState):
            raise TypeError("Demucs worker cycle next state is invalid.")

        if self.action is DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION:
            if not isinstance(self.normal_iteration, DemucsWorkerIterationResult):
                raise ValueError("A normal Demucs worker cycle requires a normal iteration result.")
            if self.recovery_iteration is not None:
                raise ValueError("A normal Demucs worker cycle cannot include recovery evidence.")
            expected_next = DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN
        elif self.action is DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN:
            if not isinstance(self.recovery_iteration, DemucsRecoveryIterationResult):
                raise ValueError("A recovery Demucs worker cycle requires a recovery iteration result.")
            if self.normal_iteration is not None:
                raise ValueError("A recovery Demucs worker cycle cannot include normal evidence.")
            expected_next = DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION
        else:  # Defensive guard for a bypassed frozen object/future vocabulary.
            raise ValueError("Demucs worker cycle action is invalid.")

        if self.next_state.next_action is not expected_next:
            raise ValueError("Demucs worker cycle next state does not follow its action.")


def run_one_demucs_worker_cycle(
    channel: Any,
    *,
    state: DemucsWorkerCadenceState,
    database: DemucsWorkerCycleDatabase,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsWorkerCycleResult:
    """Run one cadence-selected normal or recovery action and return next state.

    Operational errors propagate unchanged. Cadence state is frozen, so a
    failed branch cannot partially advance it: a later supervisor can classify
    the failure and retry the same selected action after its own bounded policy.
    This function starts no loop, waits, reconnects, or owns channel lifecycle.
    """

    if not isinstance(state, DemucsWorkerCadenceState):
        raise TypeError("state must be DemucsWorkerCadenceState.")

    if state.next_action is DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION:
        normal_iteration = receive_and_execute_demucs_once(
            channel,
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=pre_model_retry_after_seconds,
            running_retry_after_seconds=running_retry_after_seconds,
        )
        next_state = advance_after_demucs_normal_iteration(state, normal_iteration)
        return DemucsWorkerCycleResult(
            action=DemucsWorkerCadenceAction.RUN_NORMAL_ITERATION,
            next_state=next_state,
            normal_iteration=normal_iteration,
        )

    if state.next_action is DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN:
        # Do not pass `channel`: recovery has no RabbitMQ delivery, so it has
        # no acknowledgement/rejection/publish operation to perform.
        recovery_iteration = recover_and_execute_demucs_once(
            database=database,
            source_client=source_client,
            artifact_client=artifact_client,
            work_directory=work_directory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            pre_model_retry_after_seconds=pre_model_retry_after_seconds,
            running_retry_after_seconds=running_retry_after_seconds,
        )
        next_state = advance_after_demucs_recovery_iteration(state, recovery_iteration)
        return DemucsWorkerCycleResult(
            action=DemucsWorkerCadenceAction.RUN_RECOVERY_SCAN,
            next_state=next_state,
            recovery_iteration=recovery_iteration,
        )

    # Frozen-state validation rejects this in ordinary construction. Retain a
    # narrow final guard so a bypassed object cannot select unreviewed work.
    raise TypeError("Demucs worker cadence action is invalid.")
