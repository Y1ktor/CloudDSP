"""Sequentially upload one complete in-scope Demucs stem set without state writes.

The prior plan workspace holds all and only the fixed private MinIO plans for a
single running Demucs task. This composition performs the bounded network work
in the required order:

``fixed plan 1 -> matching receipt 1 -> ... -> fixed plan N -> complete receipt set``

It reuses the one-plan handoff for every item, so each local WAV is rehashed by
the restricted uploader before and while it streams. Earlier private objects
may remain at their retry-overwritable keys if a later upload fails, but this
function returns no complete set in that case and makes no PostgreSQL change.
It does not run Demucs, plan artifacts, renew/finish a lease, publish or
acknowledge RabbitMQ work, build an image, or change Kubernetes.
"""

from __future__ import annotations

from app.artifacts.demucs_artifact_upload import DemucsPutObjectClient, UploadedDemucsStemObject
from app.processing.demucs_stem_set_publish import PublishedDemucsStemSet
from app.artifacts.planned_stem_upload import (
    DemucsPlannedStemUploader,
    upload_demucs_stem_from_plan_workspace,
)
from app.artifacts.stem_output_plan_workspace import DemucsStemOutputPlanWorkspace


class DemucsCompleteStemUploadProtocolError(RuntimeError):
    """A complete-receipt result did not preserve the fixed plan order/evidence.

    This category carries no object key, task identity, local path, checksum, or
    storage diagnostic. The individual one-stem uploader keeps the more useful
    safe storage/path/consistency category when a concrete upload fails.
    """


def _plan_workspace_or_raise(value: object) -> DemucsStemOutputPlanWorkspace:
    """Require the established current hash/plan handoff before looping writes."""

    if not isinstance(value, DemucsStemOutputPlanWorkspace):
        raise TypeError("workspace must be DemucsStemOutputPlanWorkspace.")
    return value


def _complete_receipts_or_raise(
    *,
    workspace: DemucsStemOutputPlanWorkspace,
    receipts: tuple[UploadedDemucsStemObject, ...],
) -> PublishedDemucsStemSet:
    """Return success only for one receipt matching every plan in order."""

    if len(receipts) != len(workspace.output_objects):
        raise DemucsCompleteStemUploadProtocolError("Demucs complete stem upload is invalid.")
    for output_object, receipt in zip(workspace.output_objects, receipts, strict=True):
        metadata = dict(output_object.s3_metadata)
        if (
            not isinstance(receipt, UploadedDemucsStemObject)
            or receipt.bucket != output_object.bucket
            or receipt.object_key != output_object.object_key
            or receipt.content_length != output_object.content_length
            or receipt.sha256 != metadata.get("sha256")
        ):
            raise DemucsCompleteStemUploadProtocolError("Demucs complete stem upload is invalid.")
    # `PublishedDemucsStemSet` is the existing receipt-only type accepted by
    # the later guarded PostgreSQL completion transaction. Reusing it avoids a
    # parallel, slightly different durable-result shape for the same evidence.
    return PublishedDemucsStemSet(
        lease=workspace.running.lease,
        uploads=receipts,
    )


def upload_complete_demucs_stem_set_from_plan_workspace(
    *,
    workspace: DemucsStemOutputPlanWorkspace,
    client: DemucsPutObjectClient,
    uploader: DemucsPlannedStemUploader | None = None,
) -> PublishedDemucsStemSet:
    """Upload every fixed plan sequentially, or return no complete evidence.

    Callers use this inside ``opened_demucs_stem_output_plan_workspace(...)``.
    It intentionally has no concurrency: fixed plan order bounds one worker's
    local/network use, makes test evidence deterministic, and stops subsequent
    writes immediately after the first failed plan. A later guarded database
    transaction—not this function—decides whether the full returned receipt set
    may transition a task/job or create downstream outbox events.
    """

    plan_workspace = _plan_workspace_or_raise(workspace)
    receipts: list[UploadedDemucsStemObject] = []
    for output_object in plan_workspace.output_objects:
        receipts.append(
            upload_demucs_stem_from_plan_workspace(
                workspace=plan_workspace,
                output_object=output_object,
                client=client,
                uploader=uploader,
            )
        )
    return _complete_receipts_or_raise(workspace=plan_workspace, receipts=tuple(receipts))
