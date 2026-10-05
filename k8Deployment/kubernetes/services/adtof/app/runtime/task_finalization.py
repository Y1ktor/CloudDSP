"""Finalize one already-running ADTOF task after local CPU output verification.

The caller already owns ``VerifiedADTOFLocalTaskOutputs`` inside its temporary
running-stem context. This focused composition performs the remaining happy
path in the only safe order:

1. rebuild and revalidate the two complete deterministic upload plans;
2. stream both artifacts through the restricted MinIO identity;
3. prove both stored objects with metadata-only ``HeadObject`` requests; and
4. commit the lease-token-guarded ADTOF result in a short PostgreSQL context.

It returns only committed task-completion evidence. A no-row final commit is
normal ownership loss: deterministic private objects can remain from a stale
attempt, but this caller must not call the task successful or make a broker
acknowledgement decision. Exceptions propagate unchanged to a later worker
supervisor, which owns retry, terminal-failure, and RabbitMQ policy.

This is not an AMQP worker loop. It does not download/start a task, invoke a
model, create a connection, apply the database bootstrap Job, receive or
acknowledge a delivery, classify an error, or use the Kubernetes API.
"""

from __future__ import annotations

from typing import Protocol

from app.processing.local_task_execution import VerifiedADTOFLocalTaskOutputs
from app.artifacts.minio_upload import ADTOFPutObjectClient, upload_adtof_objects
from app.artifacts.output_artifact_head_object import (
    ADTOFOutputHeadObjectClient,
    verify_uploaded_adtof_output_head_objects,
)
from app.db.task_completion import ADTOFTaskCompletion
from app.db.task_completion_commit import ADTOFTaskCompletionDatabase, commit_verified_adtof_task
from app.artifacts.upload_object import build_adtof_upload_objects


class ADTOFTaskFinalizationStorageClient(
    ADTOFPutObjectClient,
    ADTOFOutputHeadObjectClient,
    Protocol,
):
    """The exact private MinIO surface allowed after ADTOF inference succeeds.

    Combining the two existing narrow protocols documents that finalization may
    write and then inspect only its reviewed deterministic output keys. The
    permanent MinIO policy remains the enforcing layer; this interface grants
    no listing, deletion, presigning, source-upload, or arbitrary-object API.
    """


def finalize_running_adtof_task(
    *,
    database: ADTOFTaskCompletionDatabase,
    storage_client: ADTOFTaskFinalizationStorageClient,
    local_outputs: VerifiedADTOFLocalTaskOutputs,
) -> ADTOFTaskCompletion | None:
    """Persist one local verified result, or return normal final ownership loss.

    Call this only before the surrounding running-stem context removes its
    scratch directory. Each lower boundary repeats its own plan/path/object
    validation, so this coordinator does not create a broader authority merely
    by composing them. No long PostgreSQL transaction surrounds MinIO I/O: the
    final commit helper opens its own short scope only after both objects have
    stored-object proof.
    """

    if not isinstance(local_outputs, VerifiedADTOFLocalTaskOutputs):
        raise TypeError("local_outputs must be VerifiedADTOFLocalTaskOutputs.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    for method_name in ("put_object", "head_object"):
        if not callable(getattr(storage_client, method_name, None)):
            raise TypeError("storage_client does not provide the required MinIO operations.")

    # Both paths remain private Pod scratch here. The plan builder repeats the
    # local-byte proof; the uploader then performs a second streamed proof.
    upload_objects = build_adtof_upload_objects(local_outputs=local_outputs)
    upload_receipts = upload_adtof_objects(client=storage_client, upload_objects=upload_objects)
    stored_outputs = verify_uploaded_adtof_output_head_objects(
        storage_client,
        upload_objects=upload_objects,
        upload_receipts=upload_receipts,
    )
    # This is the sole transaction in finalization. A return from this helper
    # is post-commit; a None indicates the current lease no longer owns state.
    return commit_verified_adtof_task(
        database=database,
        lease=local_outputs.running.lease,
        stored_outputs=stored_outputs,
        tempo_candidate=local_outputs.tempo_candidate,
    )
