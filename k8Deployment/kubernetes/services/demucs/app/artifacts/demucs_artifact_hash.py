"""Stream SHA-256 evidence for one already validated local Demucs stem inventory.

The artifact-inventory boundary proves exact names, regular-file paths, and
non-zero byte counts. This next layer re-runs that inventory check immediately
before it opens files, then streams each file through SHA-256 without loading a
stem into memory. The returned digests let a later MinIO uploader record and
verify exactly which bytes it handled.

It does not parse WAV audio, upload/delete/list objects, invoke Demucs, alter a
lease or task result, open a database/broker connection, or interact with
Kubernetes. Hashes are local evidence only until a later narrow upload boundary
uses them with the restricted Demucs MinIO identity.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.artifacts.demucs_artifacts import (
    ValidatedDemucsStemArtifact,
    ValidatedDemucsStemInventory,
    validate_demucs_stem_inventory,
)
from app.processing.demucs_command import DemucsSeparationCommand


# A bounded streaming read keeps the worker's memory independent of WAV size.
# The earlier inventory supplies the exact expected byte count, while Pod scratch
# limits the maximum retained local data across all stages.
DEMUCS_ARTIFACT_HASH_CHUNK_BYTES = 64 * 1024


class DemucsArtifactHashError(RuntimeError):
    """Base safe category for local stem-hash failures."""


class DemucsArtifactHashContractError(DemucsArtifactHashError):
    """The supplied inventory is stale, hand-built, or no longer validates."""


class DemucsArtifactHashPathError(DemucsArtifactHashError):
    """A validated artifact could not be opened as a current regular file."""


class DemucsArtifactHashConsistencyError(DemucsArtifactHashError):
    """A stem changed size while hash evidence was being established."""


@dataclass(frozen=True)
class HashedDemucsStemArtifact:
    """A named ephemeral local stem plus the SHA-256 evidence for its exact bytes."""

    stem_name: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class HashedDemucsStemInventory:
    """Complete ordered stem evidence available to the future MinIO upload layer."""

    separation: DemucsSeparationCommand
    artifacts: tuple[HashedDemucsStemArtifact, ...]


def _current_inventory_or_raise(value: object) -> ValidatedDemucsStemInventory:
    """Revalidate local files so a stale inventory cannot cross into hashing."""

    if not isinstance(value, ValidatedDemucsStemInventory):
        raise DemucsArtifactHashContractError("Demucs artifact hash inventory is invalid.")
    try:
        current = validate_demucs_stem_inventory(value.separation)
    except Exception as error:
        # Do not expose a scratch path or unexpected file name in a later
        # durable error category. The original exception remains chained for
        # local diagnostics, while the public category stays stable.
        raise DemucsArtifactHashContractError("Demucs artifact hash inventory is invalid.") from error
    if current != value:
        raise DemucsArtifactHashContractError("Demucs artifact hash inventory is invalid.")
    return current


def _hash_current_artifact(artifact: ValidatedDemucsStemArtifact) -> HashedDemucsStemArtifact:
    """Hash one already revalidated stem while requiring its bytes stay stable."""

    if not isinstance(artifact, ValidatedDemucsStemArtifact) or not isinstance(artifact.path, Path):
        raise DemucsArtifactHashContractError("Demucs artifact hash inventory is invalid.")
    try:
        if artifact.path.is_symlink():
            raise DemucsArtifactHashPathError("Demucs artifact hash path is invalid.")
        with artifact.path.open("rb", buffering=0) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.size_bytes:
                raise DemucsArtifactHashConsistencyError("Demucs artifact changed before hashing.")
            hasher = hashlib.sha256()
            bytes_read = 0
            while chunk := source.read(DEMUCS_ARTIFACT_HASH_CHUNK_BYTES):
                hasher.update(chunk)
                bytes_read += len(chunk)
            after = os.fstat(source.fileno())
    except DemucsArtifactHashError:
        raise
    except OSError as error:
        raise DemucsArtifactHashPathError("Demucs artifact hash path is invalid.") from error
    if bytes_read != artifact.size_bytes or after.st_size != artifact.size_bytes:
        raise DemucsArtifactHashConsistencyError("Demucs artifact changed while hashing.")
    return HashedDemucsStemArtifact(
        stem_name=artifact.stem_name,
        path=artifact.path,
        size_bytes=artifact.size_bytes,
        sha256=hasher.hexdigest(),
    )


def hash_validated_demucs_stem_inventory(
    inventory: ValidatedDemucsStemInventory,
) -> HashedDemucsStemInventory:
    """Return SHA-256 evidence only for the exact current local stem inventory.

    A caller must invoke this after the process runner has returned and before a
    future uploader opens the files. Revalidating the inventory detects a file
    that was added, removed, renamed, symlinked, emptied, or resized since the
    prior result. The per-file stream check then confirms the exact recorded
    byte count before and after hashing.
    """

    current_inventory = _current_inventory_or_raise(inventory)
    return HashedDemucsStemInventory(
        separation=current_inventory.separation,
        artifacts=tuple(_hash_current_artifact(artifact) for artifact in current_inventory.artifacts),
    )
