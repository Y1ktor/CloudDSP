"""Run one already-claimed ADTOF task through the reviewed success path.

This is a post-claim coordinator, not a RabbitMQ consumer. Its caller must have
already parsed a delivery, committed the durable ADTOF first-claim decision,
and selected the resulting ``claimed`` lease. For that one lease it preserves
the required success-path order:

1. verify the private Demucs drums stem with MinIO ``HeadObject``;
2. stream/hash-download it and commit ``leased`` -> ``running``;
3. run the fixed CPU ADTOF command and verify both local output files; and
4. upload, prove stored outputs, and commit guarded task completion before the
   temporary running-stem context removes all scratch files.

Only committed success or normal ownership loss can return. Any MinIO,
database, CPU, output-validation, or completion error propagates to a later
supervisor, which will define retry and terminal-failure policy. This module
does not parse deliveries, receive/acknowledge/reject RabbitMQ messages,
schedule retries, create a Deployment, or use the Kubernetes API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.processing.adtof_cpu_process import DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS, ADTOFCPUProcessRunner
from app.messaging.adtof_requested_message import ADTOFRequestedMessage
from app.processing.local_task_execution import execute_running_adtof_local_task
from app.artifacts.stem_download import ADTOFGetObjectClient
from app.artifacts.stem_object import ADTOFHeadObjectClient, verify_claimed_adtof_stem_head_object
from app.db.stem_task_start import ADTOFTaskStartDatabase, started_verified_adtof_stem
from app.db.task_claim import ADTOFTaskLease
from app.db.task_completion import ADTOFTaskCompletion
from app.db.task_completion_commit import ADTOFTaskCompletionDatabase
from app.runtime.task_finalization import ADTOFTaskFinalizationStorageClient, finalize_running_adtof_task


class ADTOFClaimedTaskSuccessStorageClient(
    ADTOFHeadObjectClient,
    ADTOFGetObjectClient,
    ADTOFTaskFinalizationStorageClient,
    Protocol,
):
    """The exact MinIO calls allowed across one post-claim success attempt.

    The combined protocol is descriptive rather than an authorization grant.
    The restricted MinIO identity still enforces the actual drums-stem read and
    deterministic output put/head object policy; this coordinator has no list,
    delete, presign, or arbitrary source-upload capability.
    """


class ADTOFClaimedTaskSuccessDatabase(
    ADTOFTaskStartDatabase,
    ADTOFTaskCompletionDatabase,
    Protocol,
):
    """One restricted provider for the separate short start/complete transactions."""


class ADTOFClaimedTaskSuccessOutcome(StrEnum):
    """The only normal outcomes after a durable claim reaches this coordinator."""

    SUCCEEDED = "succeeded"
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True)
class ADTOFClaimedTaskSuccess:
    """Non-sensitive result after one post-claim happy-path attempt.

    It intentionally contains no scratch path, object key, tempo JSON,
    credential, delivery tag, or raw storage/database response. Those values
    belong only to lower private boundaries and must not cross into future
    broker logging or metrics as task-completion evidence.
    """

    outcome: ADTOFClaimedTaskSuccessOutcome
    completion: ADTOFTaskCompletion | None = None

    def __post_init__(self) -> None:
        """Keep success and ownership-loss result shapes unambiguous."""

        if self.outcome is ADTOFClaimedTaskSuccessOutcome.SUCCEEDED:
            if not isinstance(self.completion, ADTOFTaskCompletion):
                raise TypeError("A successful ADTOF task requires completion evidence.")
            return
        if self.outcome is ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST:
            if self.completion is not None:
                raise TypeError("A lost ADTOF lease cannot include completion evidence.")
            return
        raise TypeError("ADTOF claimed task success outcome is invalid.")


def execute_claimed_adtof_task_success_path(
    *,
    database: ADTOFClaimedTaskSuccessDatabase,
    storage_client: ADTOFClaimedTaskSuccessStorageClient,
    message: ADTOFRequestedMessage,
    lease: ADTOFTaskLease,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    process_runner: ADTOFCPUProcessRunner | None = None,
) -> ADTOFClaimedTaskSuccess:
    """Run one committed claim through success, or stop when it loses ownership.

    The `message`/`lease` must originate from the same committed first-claim
    outcome. Every lower boundary repeats the fixed task/Job/stem coordinate
    before I/O, so direct dataclass construction cannot widen this coordinator's
    storage or completion scope. This function never catches operational errors:
    a later supervisor must see their reviewed type before it chooses a durable
    retry/failure transition or any AMQP acknowledgement action.
    """

    if not isinstance(message, ADTOFRequestedMessage):
        raise TypeError("message must be ADTOFRequestedMessage.")
    if not isinstance(lease, ADTOFTaskLease):
        raise TypeError("lease must be ADTOFTaskLease.")
    if not isinstance(work_directory, Path):
        raise TypeError("work_directory must be pathlib.Path.")
    if type(process_timeout_seconds) is not int:
        raise TypeError("process_timeout_seconds must be an integer.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    for method_name in ("head_object", "get_object", "put_object"):
        if not callable(getattr(storage_client, method_name, None)):
            raise TypeError("storage_client does not provide the required MinIO operations.")

    # Metadata validation happens before the downloaded bytes and before CPU
    # work. The start context repeats the stream hash and commits `running`
    # before yielding a scratch path, so an ownership-lost worker receives no
    # path that it could accidentally pass to the model.
    verified_stem = verify_claimed_adtof_stem_head_object(
        storage_client,
        lease=lease,
        message=message,
    )
    with started_verified_adtof_stem(
        database=database,
        client=storage_client,
        lease=lease,
        source=verified_stem,
        work_directory=work_directory,
    ) as running:
        if running is None:
            return ADTOFClaimedTaskSuccess(
                outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
            )

        # `running` holds paths scoped to this `with` block. Local output and
        # finalization must remain nested here so output plans never point at a
        # scratch tree that has already been cleaned up.
        local_outputs = execute_running_adtof_local_task(
            running=running,
            work_directory=work_directory,
            process_timeout_seconds=process_timeout_seconds,
            process_runner=process_runner,
        )
        completion = finalize_running_adtof_task(
            database=database,
            storage_client=storage_client,
            local_outputs=local_outputs,
        )

    if completion is None:
        return ADTOFClaimedTaskSuccess(
            outcome=ADTOFClaimedTaskSuccessOutcome.OWNERSHIP_LOST,
        )
    return ADTOFClaimedTaskSuccess(
        outcome=ADTOFClaimedTaskSuccessOutcome.SUCCEEDED,
        completion=completion,
    )
