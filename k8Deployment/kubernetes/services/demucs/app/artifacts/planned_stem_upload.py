"""Upload exactly one in-scope Demucs object plan through the restricted adapter.

The preceding plan workspace has already established a current local WAV path,
its SHA-256 evidence, and the only private MinIO bucket/key/metadata tuple it
may use. This module makes one deliberately narrow side-effecting handoff:

``one exact plan instance -> streaming PutObject -> matching durable-safe receipt``

It never chooses an arbitrary key or local path. The lower uploader re-hashes
the file before and during MinIO streaming; this wrapper also refuses a receipt
that does not exactly match the selected in-scope plan. It does not aggregate a
complete set, mutate PostgreSQL, renew/finish a lease, publish or acknowledge
RabbitMQ work, build an image, or change Kubernetes.
"""

from __future__ import annotations

from typing import Protocol

from app.artifacts.demucs_artifact_upload import (
    DemucsPutObjectClient,
    UploadedDemucsStemObject,
    upload_demucs_stem_object,
)
from app.artifacts.demucs_output_object import DemucsStemOutputObject
from app.artifacts.stem_output_plan_workspace import DemucsStemOutputPlanWorkspace


class DemucsPlannedStemUploadWorkspaceProtocolError(RuntimeError):
    """A selected plan or returned receipt is not tied to the open workspace.

    This fixed category intentionally omits object keys, task IDs, local paths,
    SHA-256 values, credentials, and SDK diagnostics. The lower adapter's
    storage/path/consistency categories remain available for a later retry or
    terminal-result policy.
    """


class DemucsPlannedStemUploader(Protocol):
    """The exact one-plan uploader shape accepted by this composition boundary."""

    def __call__(
        self,
        *,
        client: DemucsPutObjectClient,
        output_object: DemucsStemOutputObject,
    ) -> UploadedDemucsStemObject:
        """Write one fixed plan and return only its durable-safe receipt."""


def _plan_workspace_or_raise(value: object) -> DemucsStemOutputPlanWorkspace:
    """Require the current local hash/output-plan chain before a MinIO write."""

    if not isinstance(value, DemucsStemOutputPlanWorkspace):
        raise TypeError("workspace must be DemucsStemOutputPlanWorkspace.")
    return value


def _selected_plan_or_raise(
    *,
    workspace: DemucsStemOutputPlanWorkspace,
    output_object: object,
) -> DemucsStemOutputObject:
    """Accept only the exact plan instance yielded by this workspace.

    Identity prevents a caller from cloning a frozen dataclass and changing it
    later before calling an injected uploader. The output-plan workspace already
    validates every original item against current hashed evidence, so there is
    no browser-derived stem/key selector at this boundary.
    """

    if not isinstance(output_object, DemucsStemOutputObject):
        raise TypeError("output_object must be DemucsStemOutputObject.")
    if not any(output_object is candidate for candidate in workspace.output_objects):
        raise DemucsPlannedStemUploadWorkspaceProtocolError(
            "Demucs planned stem upload workspace is invalid."
        )
    return output_object


def _matching_receipt_or_raise(
    *,
    output_object: DemucsStemOutputObject,
    receipt: object,
) -> UploadedDemucsStemObject:
    """Ensure an injected/default uploader proves the selected plan only."""

    if not isinstance(receipt, UploadedDemucsStemObject):
        raise DemucsPlannedStemUploadWorkspaceProtocolError(
            "Demucs planned stem upload workspace is invalid."
        )
    metadata = dict(output_object.s3_metadata)
    if (
        receipt.bucket != output_object.bucket
        or receipt.object_key != output_object.object_key
        or receipt.content_length != output_object.content_length
        or receipt.sha256 != metadata.get("sha256")
    ):
        raise DemucsPlannedStemUploadWorkspaceProtocolError(
            "Demucs planned stem upload workspace is invalid."
        )
    return receipt


def upload_demucs_stem_from_plan_workspace(
    *,
    workspace: DemucsStemOutputPlanWorkspace,
    output_object: DemucsStemOutputObject,
    client: DemucsPutObjectClient,
    uploader: DemucsPlannedStemUploader | None = None,
) -> UploadedDemucsStemObject:
    """Upload one selected current plan and return its matching receipt only.

    The caller invokes this inside
    ``opened_demucs_stem_output_plan_workspace(...)`` while its local artifact
    still exists. Passing no ``uploader`` selects the reviewed streaming/hash
    MinIO adapter. Tests may inject a compatible narrow function, but its
    receipt still has to match the original plan exactly. A future sequential
    composition will call this once per fixed plan and decide separately when a
    complete receipt set may reach PostgreSQL.
    """

    plan_workspace = _plan_workspace_or_raise(workspace)
    selected_plan = _selected_plan_or_raise(
        workspace=plan_workspace,
        output_object=output_object,
    )
    selected_uploader = upload_demucs_stem_object if uploader is None else uploader
    if not callable(selected_uploader):
        raise TypeError("uploader must be callable.")
    receipt = selected_uploader(client=client, output_object=selected_plan)
    return _matching_receipt_or_raise(output_object=selected_plan, receipt=receipt)
