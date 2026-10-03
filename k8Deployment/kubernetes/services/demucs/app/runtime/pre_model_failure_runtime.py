"""Apply committed pre-model source-failure policy to one Demucs task attempt.

``task_runtime_once.py`` owns the complete happy-path attempt. It deliberately
lets every exception escape so individual source, model, artifact, and database
boundaries remain visible. This small outer handoff gives *only* the reviewed
source-preflight exceptions a durable outcome:

* an immutable source/media mismatch becomes a terminal task and Job result;
* a known temporary MinIO failure becomes a later retry on attempts one/two,
  or terminal retry exhaustion on attempt three; and
* a no-row result from that guarded update is ordinary ownership loss.

All other exceptions escape unchanged. In particular, the handoff must not
convert a post-``running`` model/output/upload failure into a pre-model result,
or turn a database/protocol/programming failure into an unreviewed retry. The
exception types recognized by ``source_failure_classification.py`` are emitted
only before ``task_runtime_once.py`` reaches its guarded ``leased -> running``
transition, which makes this narrow mapping safe.

This module receives no AMQP channel, does not poll/recover or itself schedule
renewal, does not sleep, and does not create an image, Deployment, or
Kubernetes resource. Its nested one-task runtime owns short renewal checkpoints
while a model child runs. A later long-running supervisor selects an
acknowledged delivery or recovered lease and invokes this one-attempt policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient
from app.processing.demucs_process import DemucsProcessRunner
from app.db.demucs_task_completion import CommittedDemucsStemSet
from app.processing.ffprobe_process import DemucsFFprobeRunner
from app.artifacts.planned_stem_upload import DemucsPlannedStemUploader
from app.db.preflight_task_start import DemucsTaskStartDatabase
from app.db.pre_model_failure_transition import (
    DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    DemucsPreModelFailureTransition,
    DemucsPreModelFailureTransitionDisposition,
)
from app.db.pre_model_failure_transition_commit import (
    DemucsPreModelFailureTransitionDatabase,
    commit_leased_demucs_pre_model_failure_transition,
)
from app.db.running_failure_transition import (
    DemucsRunningFailureTransition,
    DemucsRunningFailureTransitionDisposition,
)
from app.runtime.source_failure_classification import (
    DemucsPreModelFailureDisposition,
    classify_demucs_pre_model_failure,
)
from app.processing.source_preflight import DemucsSourcePreflightClient
from app.db.task_lease import DemucsTaskLease
from app.runtime.task_runtime_once import execute_acknowledged_demucs_task_once


class DemucsPreModelFailureRuntimeDatabase(
    DemucsTaskStartDatabase,
    DemucsPreModelFailureTransitionDatabase,
    Protocol,
):
    """The existing short-transaction capability shared by both boundaries.

    The concrete restricted Psycopg adapter satisfies both parent protocols
    structurally. This combined protocol documents that the normal runtime and
    the failure decision use the same authoritative database, while each still
    opens its own short transaction.
    """


class DemucsOneTaskExecutionOutcome(StrEnum):
    """The durable-safe result categories a future supervisor can act upon."""

    SUCCEEDED = "succeeded"
    OWNERSHIP_LOST = "ownership_lost"
    RETRY_SCHEDULED = "retry_scheduled"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(frozen=True)
class DemucsOneTaskExecution:
    """One task-attempt outcome with exactly the matching committed evidence.

    The result intentionally contains no exception object, MinIO coordinate,
    AMQP delivery tag, temporary path, token, or credential. A future
    supervisor can use the outcome to choose cadence/logging behavior without
    accidentally retaining private details from the failed source operation.
    """

    outcome: DemucsOneTaskExecutionOutcome
    completion: CommittedDemucsStemSet | None = None
    # A later, deliberately separate running-failure handoff may return the
    # same compact worker-facing outcome after CPU/output work has begun. Both
    # transition types are durable PostgreSQL evidence, but their predicates
    # differ (`leased` before model start versus `running` afterward), so this
    # union must retain the concrete result rather than flattening its codes.
    failure_transition: DemucsPreModelFailureTransition | DemucsRunningFailureTransition | None = None

    def __post_init__(self) -> None:
        """Require exactly one evidence type whenever durable work completed."""

        if self.outcome is DemucsOneTaskExecutionOutcome.SUCCEEDED:
            if not isinstance(self.completion, CommittedDemucsStemSet) or self.failure_transition is not None:
                raise TypeError("A successful Demucs task requires only completion evidence.")
            return
        if self.outcome is DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST:
            if self.completion is not None or self.failure_transition is not None:
                raise TypeError("Demucs ownership loss cannot include task evidence.")
            return
        if self.outcome is DemucsOneTaskExecutionOutcome.RETRY_SCHEDULED:
            if (
                self.completion is not None
                or not _is_committed_demucs_retry_transition(self.failure_transition)
            ):
                raise TypeError("A scheduled Demucs retry requires only matching retry evidence.")
            return
        if self.outcome is DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE:
            if (
                self.completion is not None
                or not _is_committed_demucs_terminal_transition(self.failure_transition)
            ):
                raise TypeError("A terminal Demucs task requires only matching terminal evidence.")
            return
        raise TypeError("Demucs one-task execution outcome is invalid.")


def _is_committed_demucs_retry_transition(value: object) -> bool:
    """Accept only the matching durable retry result from either reviewed phase.

    The shared worker-facing outcome does not erase the phase. Each concrete
    transition retains its own lease predicate and finite failure vocabulary,
    while this narrow predicate merely prevents a terminal/no-row object from
    being presented as a scheduled retry.
    """

    return (
        isinstance(value, DemucsPreModelFailureTransition)
        and value.disposition is DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED
    ) or (
        isinstance(value, DemucsRunningFailureTransition)
        and value.disposition is DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED
    )


def _is_committed_demucs_terminal_transition(value: object) -> bool:
    """Accept only the matching durable terminal result from either phase."""

    return (
        isinstance(value, DemucsPreModelFailureTransition)
        and value.disposition is DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE
    ) or (
        isinstance(value, DemucsRunningFailureTransition)
        and value.disposition is DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE
    )


def _acknowledged_lease_for_failure_transition(receive_result: object) -> DemucsTaskLease:
    """Recover only the exact already-acknowledged lease from the receive result.

    The manual-ack result is the durable record that PostgreSQL created this
    canonical lease before RabbitMQ accepted its acknowledgement. The normal
    runtime's source workspace consumes the same object. Reading it here after
    a classified pre-model exception does not reconstruct an owner or trust a
    message body; it merely gives the subsequent guarded SQL update the token
    that the source check actually used.
    """

    if (
        not isinstance(receive_result, DemucsConsumeOneResult)
        or receive_result.outcome is not DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE
        or not isinstance(receive_result.lease, DemucsTaskLease)
    ):
        raise TypeError("A Demucs source failure requires an acknowledged task lease.")
    return receive_result.lease


def execute_acknowledged_demucs_task_with_pre_model_failure_policy(
    *,
    receive_result: DemucsConsumeOneResult,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    database: DemucsPreModelFailureRuntimeDatabase,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
) -> DemucsOneTaskExecution:
    """Run one acknowledged task and commit a reviewed pre-model failure only.

    A normal completion or normal ownership loss from the established runtime
    becomes the matching compact result. On exception, this helper first asks
    the strict source classifier whether it has an explicitly reviewed
    pre-``running`` category. An unclassified error is raised unchanged. A
    classified result is then committed through its independent short database
    transaction; a no-row commit race is ownership loss rather than a second
    durable action.

    No transaction spans the full attempt: the inner runtime owns its separate
    `running` and success transactions, and the failure wrapper opens a third
    short scope only after a source error unwinds all scratch contexts. This
    function does not receive/acknowledge RabbitMQ, retry an exception, sleep,
    recover due work, or renew a lease.
    """

    try:
        completion = execute_acknowledged_demucs_task_once(
            receive_result=receive_result,
            source_client=source_client,
            artifact_client=artifact_client,
            database=database,
            work_directory=work_directory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
        )
    except Exception as execution_error:
        # The classifier itself performs no I/O and recognizes only source
        # failure wrappers emitted before `leased -> running`. Do not catch
        # unclassified errors here: preserving their original type/cause is
        # necessary for the later supervisor's distinct policy.
        classification = classify_demucs_pre_model_failure(execution_error)
        if classification.disposition is DemucsPreModelFailureDisposition.UNCLASSIFIED:
            raise

        transition = commit_leased_demucs_pre_model_failure_transition(
            database=database,
            lease=_acknowledged_lease_for_failure_transition(receive_result),
            classification=classification,
            retry_after_seconds=retry_after_seconds,
        )
        if transition is None:
            return DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        if transition.disposition is DemucsPreModelFailureTransitionDisposition.RETRY_SCHEDULED:
            return DemucsOneTaskExecution(
                outcome=DemucsOneTaskExecutionOutcome.RETRY_SCHEDULED,
                failure_transition=transition,
            )
        if transition.disposition is DemucsPreModelFailureTransitionDisposition.TERMINAL_FAILURE:
            return DemucsOneTaskExecution(
                outcome=DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE,
                failure_transition=transition,
            )
        raise RuntimeError("Demucs committed source-failure transition is invalid.")

    if completion is None:
        return DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
    return DemucsOneTaskExecution(
        outcome=DemucsOneTaskExecutionOutcome.SUCCEEDED,
        completion=completion,
    )
