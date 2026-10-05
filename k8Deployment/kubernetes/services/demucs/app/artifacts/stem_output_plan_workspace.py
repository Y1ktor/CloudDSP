"""Bind deterministic private MinIO plans to one open hashed Demucs workspace.

The preceding workspace provides current SHA-256 evidence for every exact stem
of one committed-running task. This module performs the next pure handoff while
all those local files are still inside their temporary output scope:

``hashed stem inventory + running lease -> exact private MinIO object plans``

The existing planner repeats the current local inventory/hash proof and derives
stable ``stems/{job_id}/{stem_name}.wav`` coordinates. This wrapper preserves
the path/name/size/digest relationship so the next, separate uploader can only
receive a plan for the same in-scope evidence. It does not construct a MinIO
client, transfer bytes, alter PostgreSQL, renew a lease, interact with
RabbitMQ, build an image, or change Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.artifacts.demucs_output_object import (
    DEMUCS_ARTIFACT_CONTENT_TYPE,
    DEMUCS_ARTIFACT_KEY_PREFIX,
    DEMUCS_ARTIFACT_PRODUCER,
    DEMUCS_ARTIFACT_SCHEMA_VERSION,
    DEMUCS_STEM_FILE_EXTENSION,
    LOCAL_DEMUCS_ARTIFACT_BUCKET,
    DemucsStemOutputObject,
    build_demucs_stem_output_objects,
)
from app.artifacts.hashed_stem_inventory_workspace import DemucsHashedStemInventoryWorkspace
from app.db.preflight_task_start import DemucsRunningSource


class DemucsStemOutputPlanWorkspaceProtocolError(RuntimeError):
    """Object plans did not retain the exact running-task hash evidence.

    The public category intentionally contains no bucket credential, task ID,
    local path, checksum, or object key. Lower planning/file errors retain
    their own bounded categories; this error protects only composition pairing.
    """


@dataclass(frozen=True)
class DemucsStemOutputPlanWorkspace:
    """Private object plans for one in-scope hashed Demucs stem collection.

    ``output_objects`` is a plan, not a transfer receipt: no object has reached
    MinIO merely because this value exists. Its ``local_path`` entries remain
    temporary and become invalid when the outer execution workspace exits. The
    retained hash workspace keeps the lease and current digest evidence available
    to a later one-stem uploader without persisting any ephemeral path.
    """

    hashed_workspace: DemucsHashedStemInventoryWorkspace
    output_objects: tuple[DemucsStemOutputObject, ...]

    def __post_init__(self) -> None:
        """Require every object plan to match one exact hashed stem in order."""

        if not isinstance(self.hashed_workspace, DemucsHashedStemInventoryWorkspace):
            raise TypeError("Demucs output plans require a hashed workspace.")
        if not isinstance(self.output_objects, tuple):
            raise TypeError("Demucs output plan evidence is invalid.")
        lease = self.hashed_workspace.running.lease
        artifacts = self.hashed_workspace.hashed_inventory.artifacts
        if len(self.output_objects) != len(artifacts):
            raise DemucsStemOutputPlanWorkspaceProtocolError(
                "Demucs output plan workspace is invalid."
            )
        for output_object, artifact in zip(self.output_objects, artifacts, strict=True):
            if not isinstance(output_object, DemucsStemOutputObject):
                raise TypeError("Demucs output plan evidence is invalid.")
            expected_metadata = (
                ("schema-version", DEMUCS_ARTIFACT_SCHEMA_VERSION),
                ("producer", DEMUCS_ARTIFACT_PRODUCER),
                ("job-id", lease.job_id),
                ("task-id", lease.task_id),
                ("stem-name", artifact.stem_name),
                ("stem-mode", lease.stem_mode),
                ("size-bytes", str(artifact.size_bytes)),
                ("sha256", artifact.sha256),
            )
            if (
                output_object.bucket != LOCAL_DEMUCS_ARTIFACT_BUCKET
                or output_object.object_key
                != f"{DEMUCS_ARTIFACT_KEY_PREFIX}/{lease.job_id}/{artifact.stem_name}{DEMUCS_STEM_FILE_EXTENSION}"
                or output_object.local_path != artifact.path
                or output_object.content_type != DEMUCS_ARTIFACT_CONTENT_TYPE
                or output_object.content_length != artifact.size_bytes
                or output_object.s3_metadata != expected_metadata
            ):
                raise DemucsStemOutputPlanWorkspaceProtocolError(
                    "Demucs output plan workspace is invalid."
                )

    @property
    def running(self) -> DemucsRunningSource:
        """Expose the unchanged committed lease for the later upload boundary."""

        return self.hashed_workspace.running


def _hashed_workspace_or_raise(value: object) -> DemucsHashedStemInventoryWorkspace:
    """Require exact SHA-256 evidence before plans may name a private object."""

    if not isinstance(value, DemucsHashedStemInventoryWorkspace):
        raise TypeError("workspace must be DemucsHashedStemInventoryWorkspace.")
    return value


@contextmanager
def opened_demucs_stem_output_plan_workspace(
    workspace: DemucsHashedStemInventoryWorkspace,
) -> Iterator[DemucsStemOutputPlanWorkspace]:
    """Yield exact object plans only while the outer local artifact scope exists.

    The planner itself repeats local inventory/hash verification before it
    produces a plan. This wrapper then ensures each returned plan preserves the
    hashed workspace's ordering, stable key, private bucket, metadata, and
    current file evidence. The next uploader must remain inside this nested
    scope and independently stream/hash the file during its MinIO operation.
    """

    hashed_workspace = _hashed_workspace_or_raise(workspace)
    output_objects = build_demucs_stem_output_objects(
        lease=hashed_workspace.running.lease,
        hashed_inventory=hashed_workspace.hashed_inventory,
    )
    yield DemucsStemOutputPlanWorkspace(
        hashed_workspace=hashed_workspace,
        output_objects=output_objects,
    )
