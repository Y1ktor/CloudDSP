"""Bind exact Demucs stem inventory evidence to an open execution workspace.

The preceding execution workspace has already run one approved CPU command and
still owns its private temporary output directory. This module makes the next
required decision before hashing or object storage may be considered:

``zero-exit separation workspace -> exact expected local WAV inventory``

It is deliberately a small context-shaped handoff rather than another process
or storage adapter. The outer execution workspace continues to own output
cleanup, so this inventory and every artifact path disappear together after the
caller leaves the enclosing scope. No SHA-256 digest, MinIO call, PostgreSQL
write, lease renewal, RabbitMQ action, image build, or Kubernetes change occurs
here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.artifacts.demucs_artifacts import ValidatedDemucsStemInventory, validate_demucs_stem_inventory
from app.processing.executed_separation_workspace import DemucsExecutedSeparationWorkspace
from app.db.preflight_task_start import DemucsRunningSource


class DemucsValidatedStemInventoryWorkspaceProtocolError(RuntimeError):
    """An inventory was not produced from the exact in-scope model command.

    This stable category deliberately does not expose a task ID, lease token,
    local output path, unexpected filename, or child-process diagnostic. A
    later retry/result mapper can safely distinguish an invalid composition
    from the established artifact-validator's media/output categories.
    """


@dataclass(frozen=True)
class DemucsValidatedStemInventoryWorkspace:
    """Exact local stem evidence that remains valid only in the outer scope.

    ``inventory`` comes from the existing strict output validator and contains
    only reviewed stem names, local paths, and byte counts. ``executed_workspace``
    retains the committed task lease and fixed command record for the next
    separate hashing boundary, but no durable data is written by this class.
    """

    executed_workspace: DemucsExecutedSeparationWorkspace
    inventory: ValidatedDemucsStemInventory

    def __post_init__(self) -> None:
        """Require the validator to preserve the exact executed command object."""

        if not isinstance(self.executed_workspace, DemucsExecutedSeparationWorkspace):
            raise TypeError("Demucs stem inventory requires an executed workspace.")
        if not isinstance(self.inventory, ValidatedDemucsStemInventory):
            raise TypeError("Demucs stem inventory evidence is invalid.")
        # The pure validator returns the same immutable command instance it was
        # given. Identity—not merely value equality—prevents an alternate
        # hand-built command record from being paired with these temporary files.
        if self.inventory.separation is not self.executed_workspace.separation:
            raise DemucsValidatedStemInventoryWorkspaceProtocolError(
                "Demucs stem inventory workspace is invalid."
            )

    @property
    def running(self) -> DemucsRunningSource:
        """Expose the committed running lease through the established handoff."""

        return self.executed_workspace.running


def _executed_workspace_or_raise(value: object) -> DemucsExecutedSeparationWorkspace:
    """Reject callers that bypassed the committed-running process boundary."""

    if not isinstance(value, DemucsExecutedSeparationWorkspace):
        raise TypeError("workspace must be DemucsExecutedSeparationWorkspace.")
    return value


@contextmanager
def opened_validated_demucs_stem_inventory_workspace(
    workspace: DemucsExecutedSeparationWorkspace,
) -> Iterator[DemucsValidatedStemInventoryWorkspace]:
    """Yield exact stem evidence only while the outer output directory is open.

    Callers nest this context inside
    ``opened_executed_demucs_separation_workspace(...)``. The strict validator
    rejects missing, extra, empty, non-regular, or symlinked WAV entries before
    it yields anything. The enclosing execution context removes the complete
    output tree after this block, including when validation raises, so a later
    hashing/upload task cannot reuse a stale model result accidentally.
    """

    executed_workspace = _executed_workspace_or_raise(workspace)
    inventory = validate_demucs_stem_inventory(executed_workspace.separation)
    yield DemucsValidatedStemInventoryWorkspace(
        executed_workspace=executed_workspace,
        inventory=inventory,
    )
