"""Bind SHA-256 stem evidence to the still-open validated Demucs workspace.

The prior workspace already proved that a zero-exit Demucs process produced the
complete expected set of regular, non-empty WAV files. This module performs the
next local-only handoff while that private output directory remains open:

``validated stem inventory -> current streaming SHA-256 inventory``

The existing hash boundary repeats inventory validation and streams each file,
so a changed/symlinked/missing stem cannot carry stale evidence toward an
eventual MinIO uploader. The outer execution workspace still owns all scratch
cleanup. This module does not upload an object, mutate PostgreSQL, renew a
lease, receive or acknowledge RabbitMQ work, build an image, or change the
Kubernetes cluster.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.artifacts.demucs_artifact_hash import HashedDemucsStemInventory, hash_validated_demucs_stem_inventory
from app.db.preflight_task_start import DemucsRunningSource
from app.artifacts.validated_stem_inventory_workspace import DemucsValidatedStemInventoryWorkspace


class DemucsHashedStemInventoryWorkspaceProtocolError(RuntimeError):
    """Hash evidence did not remain tied to the preceding validated workspace.

    This safe category deliberately omits private task, lease, filesystem, and
    artifact details. It guards composition mistakes separately from the lower
    hash boundary's stable local-file consistency categories.
    """


@dataclass(frozen=True)
class DemucsHashedStemInventoryWorkspace:
    """Current SHA-256 evidence for the exact local stems of one running task.

    The path/digest evidence remains ephemeral: it is usable only until the
    enclosing execution workspace cleans its random output directory. The
    retained validated workspace keeps the durable running lease available for
    the later, separate object-plan and upload boundaries without writing it
    anywhere here.
    """

    validated_workspace: DemucsValidatedStemInventoryWorkspace
    hashed_inventory: HashedDemucsStemInventory

    def __post_init__(self) -> None:
        """Require hashes to match the same command, names, paths, and sizes."""

        if not isinstance(self.validated_workspace, DemucsValidatedStemInventoryWorkspace):
            raise TypeError("Demucs hashed inventory requires a validated workspace.")
        if not isinstance(self.hashed_inventory, HashedDemucsStemInventory):
            raise TypeError("Demucs hashed inventory evidence is invalid.")
        validated_inventory = self.validated_workspace.inventory
        if self.hashed_inventory.separation is not validated_inventory.separation:
            raise DemucsHashedStemInventoryWorkspaceProtocolError(
                "Demucs hashed inventory workspace is invalid."
            )
        expected_artifacts = validated_inventory.artifacts
        actual_artifacts = self.hashed_inventory.artifacts
        if len(actual_artifacts) != len(expected_artifacts):
            raise DemucsHashedStemInventoryWorkspaceProtocolError(
                "Demucs hashed inventory workspace is invalid."
            )
        for hashed, validated in zip(actual_artifacts, expected_artifacts, strict=True):
            if (
                hashed.stem_name != validated.stem_name
                or hashed.path != validated.path
                or hashed.size_bytes != validated.size_bytes
            ):
                raise DemucsHashedStemInventoryWorkspaceProtocolError(
                    "Demucs hashed inventory workspace is invalid."
                )

    @property
    def running(self) -> DemucsRunningSource:
        """Expose the unchanged committed running ownership for later stages."""

        return self.validated_workspace.running


def _validated_workspace_or_raise(value: object) -> DemucsValidatedStemInventoryWorkspace:
    """Require the exact-output validator to precede any artifact hashing."""

    if not isinstance(value, DemucsValidatedStemInventoryWorkspace):
        raise TypeError("workspace must be DemucsValidatedStemInventoryWorkspace.")
    return value


@contextmanager
def opened_hashed_demucs_stem_inventory_workspace(
    workspace: DemucsValidatedStemInventoryWorkspace,
) -> Iterator[DemucsHashedStemInventoryWorkspace]:
    """Yield current local hash evidence while the enclosing output scope exists.

    Callers nest this inside the validated-inventory and executed-separation
    contexts. The hash adapter revalidates the exact inventory immediately
    before it opens each file, so a file changed after the prior inventory
    cannot reach the next upload-plan boundary. This wrapper adds identity and
    per-artifact pairing checks; it deliberately does not open a storage client
    or make a durable task/result decision.
    """

    validated_workspace = _validated_workspace_or_raise(workspace)
    hashed_inventory = hash_validated_demucs_stem_inventory(validated_workspace.inventory)
    yield DemucsHashedStemInventoryWorkspace(
        validated_workspace=validated_workspace,
        hashed_inventory=hashed_inventory,
    )
