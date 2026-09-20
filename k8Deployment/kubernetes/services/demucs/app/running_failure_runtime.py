"""Apply committed post-``running`` failure policy to one Demucs attempt.

``pre_model_failure_runtime.py`` already owns the narrow policy for source
preflight exceptions before PostgreSQL records ``running``. It returns a
compact durable outcome for those reviewed source failures and allows every
later exception to escape. This outer handoff catches only those later escaped
exceptions, asks the separate running classifier for a finite safe category,
and commits the token-guarded ``running -> retry_scheduled/failed`` decision.

The ordering is intentional. A pre-model exception is resolved by the inner
policy and never reaches this module. A model process/output/private-artifact
failure occurs only after the inner one-task runtime entered ``running``; the
second SQL adapter independently proves that exact state and lease are still
current. A database, completion, source-protocol, image-contract, or unknown
error remains unchanged for a future operator-visible supervisor policy.

This is still one attempt, not a worker loop. It receives or acknowledges no
RabbitMQ delivery, does not sleep/recover a lease itself, and does not change
an image, Deployment, KEDA resource, or live cluster object. Its nested model
workspace owns short periodic renewal transactions while the process runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.demucs_artifact_upload import DemucsPutObjectClient
from app.demucs_process import DemucsLeaseRenewalOwnershipLost, DemucsProcessRunner
from app.ffprobe_process import DemucsFFprobeRunner
from app.planned_stem_upload import DemucsPlannedStemUploader
from app.pre_model_failure_runtime import (
    DemucsOneTaskExecution,
    DemucsOneTaskExecutionOutcome,
    DemucsPreModelFailureRuntimeDatabase,
    execute_acknowledged_demucs_task_with_pre_model_failure_policy,
)
from app.pre_model_failure_transition import DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS
from app.running_failure_classification import (
    DemucsRunningFailureDisposition,
    classify_demucs_running_failure,
)
from app.running_failure_transition import (
    DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
    DemucsRunningFailureTransitionDisposition,
)
from app.running_failure_transition_commit import (
    DemucsRunningFailureTransitionDatabase,
    commit_running_demucs_failure_transition,
)
from app.source_preflight import DemucsSourcePreflightClient
from app.task_lease import DemucsTaskLease


class DemucsRunningFailureRuntimeDatabase(
    DemucsPreModelFailureRuntimeDatabase,
    DemucsRunningFailureTransitionDatabase,
    Protocol,
):
    """Combine normal, pre-model, and running result transaction capability.

    The restricted Psycopg adapter already exposes the same short
    ``write_cursor()`` capability to all three boundaries. This protocol makes
    the relationship explicit without giving the runtime a broad connection or
    transaction API, and each boundary still opens its own independent scope.
    """


def _acknowledged_lease_for_running_failure_transition(receive_result: object) -> DemucsTaskLease:
    """Return only the durable lease that the failed attempt actually used.

    The exact manual-ack result proved PostgreSQL created this lease before
    RabbitMQ accepted acknowledgement. This function neither parses a broker
    body nor invents an owner: it merely supplies the immutable task coordinate
    and token to the running-state SQL guard after a reviewed exception.
    """

    if (
        not isinstance(receive_result, DemucsConsumeOneResult)
        or receive_result.outcome is not DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE
        or not isinstance(receive_result.lease, DemucsTaskLease)
    ):
        raise TypeError("A Demucs running failure requires an acknowledged task lease.")
    return receive_result.lease


def execute_acknowledged_demucs_task_with_running_failure_policy(
    *,
    receive_result: DemucsConsumeOneResult,
    source_client: DemucsSourcePreflightClient,
    artifact_client: DemucsPutObjectClient,
    database: DemucsRunningFailureRuntimeDatabase,
    work_directory: Path,
    ffprobe_runner: DemucsFFprobeRunner | None = None,
    demucs_runner: DemucsProcessRunner | None = None,
    uploader: DemucsPlannedStemUploader | None = None,
    event_id_factory: Callable[[], UUID] = uuid4,
    pre_model_retry_after_seconds: int = DEFAULT_DEMUCS_PRE_MODEL_RETRY_AFTER_SECONDS,
    running_retry_after_seconds: int = DEFAULT_DEMUCS_RUNNING_RETRY_AFTER_SECONDS,
) -> DemucsOneTaskExecution:
    """Run one task and durably resolve only reviewed post-start exceptions.

    The inner pre-model handoff preserves success, ownership loss, and its own
    finite source outcomes. On an exception it intentionally did not own, this
    outer layer checks the fail-closed running classifier. An unclassified
    error is re-raised as the same exception. A classified error becomes a
    committed running retry or exhaustion result; a no-row state/lease race is
    normal ownership loss, because the stale worker must not write another
    outcome.

    The committed transition occurs only after all inner source/output scratch
    contexts have unwound. No transaction is held *across* FFprobe, Demucs CPU,
    hashing, or MinIO upload: the nested execution workspace may open a short
    periodic renewal transaction while the model child runs. This function
    makes no AMQP acknowledgement, retry delay, recovery scan, or Kubernetes
    scaling decision.
    """

    try:
        return execute_acknowledged_demucs_task_with_pre_model_failure_policy(
            receive_result=receive_result,
            source_client=source_client,
            artifact_client=artifact_client,
            database=database,
            work_directory=work_directory,
            ffprobe_runner=ffprobe_runner,
            demucs_runner=demucs_runner,
            uploader=uploader,
            event_id_factory=event_id_factory,
            retry_after_seconds=pre_model_retry_after_seconds,
        )
    except DemucsLeaseRenewalOwnershipLost:
        # The renewal-aware runner stopped the real child before it raised this
        # signal. PostgreSQL no longer recognizes the task token, so no retry,
        # terminal error, upload, or second state transition is permitted.
        return DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
    except Exception as execution_error:
        # The inner layer already consumed every reviewed pre-model exception.
        # This classifier is deliberately fail-closed, so database, completion,
        # image-contract, and unknown failures keep their original type/cause.
        classification = classify_demucs_running_failure(execution_error)
        if classification.disposition is DemucsRunningFailureDisposition.UNCLASSIFIED:
            raise

        transition = commit_running_demucs_failure_transition(
            database=database,
            lease=_acknowledged_lease_for_running_failure_transition(receive_result),
            classification=classification,
            retry_after_seconds=running_retry_after_seconds,
        )
        if transition is None:
            return DemucsOneTaskExecution(outcome=DemucsOneTaskExecutionOutcome.OWNERSHIP_LOST)
        if transition.disposition is DemucsRunningFailureTransitionDisposition.RETRY_SCHEDULED:
            return DemucsOneTaskExecution(
                outcome=DemucsOneTaskExecutionOutcome.RETRY_SCHEDULED,
                failure_transition=transition,
            )
        if transition.disposition is DemucsRunningFailureTransitionDisposition.TERMINAL_FAILURE:
            return DemucsOneTaskExecution(
                outcome=DemucsOneTaskExecutionOutcome.TERMINAL_FAILURE,
                failure_transition=transition,
            )
        raise RuntimeError("Demucs committed running-failure transition is invalid.")
