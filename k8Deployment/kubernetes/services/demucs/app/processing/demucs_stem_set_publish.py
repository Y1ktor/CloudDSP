"""Run and publish one complete verified Demucs stem set without durable state.

This composition joins the small local boundaries in their required order:
the fixed Demucs process must exit successfully, its exact expected WAV tree is
validated, every file receives current SHA-256 evidence, deterministic private
MinIO object plans are created from the durable task lease, and each plan is
uploaded with a second streaming hash.  Only after every expected upload
returns matching evidence does this module return a complete in-memory set.

It deliberately stops short of the next durable boundary.  In particular, it
does not mark a task successful, update ``jobs.stems``, create downstream
outbox events, renew a lease, acknowledge RabbitMQ, delete partial objects,
build an image, or change Kubernetes.  A later guarded PostgreSQL transaction
will receive the complete set and decide whether it still owns the task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.artifacts.demucs_artifact_hash import hash_validated_demucs_stem_inventory
from app.artifacts.demucs_artifact_upload import (
    DemucsPutObjectClient,
    UploadedDemucsStemObject,
    upload_demucs_stem_object,
)
from app.artifacts.demucs_artifacts import validate_demucs_stem_inventory
from app.processing.demucs_command import DemucsSeparationCommand
from app.artifacts.demucs_output_object import DemucsStemOutputObject, build_demucs_stem_output_objects
from app.processing.demucs_process import (
    DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    DemucsProcessRunner,
    run_demucs_separation,
)
from app.db.task_lease import DemucsTaskLease


class DemucsStemSetPublicationContractError(RuntimeError):
    """The provided lease, command, or uploader result cannot prove a full set."""


# The uploader remains injectable for unit tests and a later runtime adapter,
# but its default is the reviewed MinIO streaming/hash boundary above.  It is
# intentionally a function shape—not a broad worker interface—so this module
# cannot gain database, broker, shell, or Kubernetes capabilities by accident.
class DemucsStemUploader(Protocol):
    """The keyword-only one-stem upload shape used by this composition."""

    def __call__(
        self,
        *,
        client: DemucsPutObjectClient,
        output_object: DemucsStemOutputObject,
    ) -> UploadedDemucsStemObject:
        """Upload exactly the supplied fixed private object plan."""


@dataclass(frozen=True)
class PublishedDemucsStemSet:
    """The complete upload evidence kept in memory until a later DB transition.

    The durable lease remains alongside receipts because the next PostgreSQL
    update must include its exact current token.  Receipts retain only stable
    object coordinates, byte counts, and SHA-256 values—never scratch paths,
    MinIO credentials, or raw client responses.
    """

    lease: DemucsTaskLease
    uploads: tuple[UploadedDemucsStemObject, ...]


def _contract_error() -> DemucsStemSetPublicationContractError:
    """Return one non-sensitive category for an invalid publication handoff."""

    return DemucsStemSetPublicationContractError("Demucs stem-set publication contract is invalid.")


def _matching_running_identity(lease: object, separation: object) -> tuple[DemucsTaskLease, DemucsSeparationCommand]:
    """Stop before model execution if the task and command select different modes."""

    if not isinstance(lease, DemucsTaskLease) or not isinstance(separation, DemucsSeparationCommand):
        raise _contract_error()
    # The lower command/process builder and output-object boundary each repeat
    # deeper validation.  This early equality gate specifically avoids spending
    # CPU or writing any artifact when a caller pairs the wrong durable task
    # with an otherwise valid local command.
    if lease.stem_mode != separation.stem_mode:
        raise _contract_error()
    return lease, separation


def _matching_upload_receipt(
    *,
    output_object: DemucsStemOutputObject,
    receipt: object,
) -> UploadedDemucsStemObject:
    """Require an injected uploader's receipt to match its one immutable plan."""

    if not isinstance(receipt, UploadedDemucsStemObject):
        raise _contract_error()
    metadata = dict(output_object.s3_metadata)
    if (
        receipt.bucket != output_object.bucket
        or receipt.object_key != output_object.object_key
        or receipt.content_length != output_object.content_length
        or receipt.sha256 != metadata.get("sha256")
    ):
        raise _contract_error()
    return receipt


def run_and_publish_demucs_stem_set(
    *,
    lease: DemucsTaskLease,
    separation: DemucsSeparationCommand,
    client: DemucsPutObjectClient,
    timeout_seconds: int = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    runner: DemucsProcessRunner | None = None,
    uploader: DemucsStemUploader | None = None,
) -> PublishedDemucsStemSet:
    """Execute and upload every expected stem, or raise before any success state.

    The caller must already have a committed ``running`` lease and a safe local
    command whose source file remains in its worker-owned scratch directory.
    This function runs no concurrent uploads: a predictable sequential order
    keeps one worker's memory/network use bounded and lets a failure stop the
    remaining writes.  Earlier successful writes can remain private at their
    stable retry-overwritable keys, but no result is returned until the full
    set completes, so this function itself cannot make partial artifacts
    browser-visible or durable in PostgreSQL.
    """

    current_lease, requested_separation = _matching_running_identity(lease, separation)
    selected_uploader = upload_demucs_stem_object if uploader is None else uploader
    if not callable(selected_uploader):
        raise TypeError("uploader must be callable.")

    completed_separation = run_demucs_separation(
        requested_separation,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    # Each result feeds the next stricter boundary.  None of these values is
    # persisted here: all local paths disappear once the worker scratch scope
    # is cleaned up by the future outer runtime.
    inventory = validate_demucs_stem_inventory(completed_separation)
    hashed_inventory = hash_validated_demucs_stem_inventory(inventory)
    output_objects = build_demucs_stem_output_objects(
        lease=current_lease,
        hashed_inventory=hashed_inventory,
    )

    uploads: list[UploadedDemucsStemObject] = []
    for output_object in output_objects:
        # The default uploader repeats its own plan validation and streaming
        # byte proof.  Checking the receipt again makes the composition safe
        # for an injected unit-test/future adapter that returns an incorrect
        # object after a nominal call.
        receipt = selected_uploader(client=client, output_object=output_object)
        uploads.append(
            _matching_upload_receipt(
                output_object=output_object,
                receipt=receipt,
            )
        )
    return PublishedDemucsStemSet(lease=current_lease, uploads=tuple(uploads))
