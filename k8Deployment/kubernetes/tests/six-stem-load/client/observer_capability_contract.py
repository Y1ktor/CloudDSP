"""Derive the observer's exact scope from an in-process ownership proof.

This is deliberately a pure, source-only contract. It does not create a
PostgreSQL role, MinIO user/policy, RabbitMQ user, Kubernetes ServiceAccount,
Secret, file, network request, or subprocess. The lifecycle broker passes this
immutable structure to separate database/object provisioners only after it has
performed its owner-bound API verification.

The nearby ``verified-authenticated-ingress.json`` handoff marker is *not* an
input here. It is writable by the two same-UID containers and therefore cannot
authorize access. This function accepts the broker's in-memory
``VerifiedOwnerBoundLoadJobs`` result so capability creation cannot accidentally
be driven by a marker, a caller-provided UUID list, or a browser request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from uuid import UUID

from lifecycle_handoff import AuthenticatedLoadCoordinates, SubmittedLoadJobCoordinate
from owner_bound_job_verifier import VerifiedOwnerBoundLoadJobs


_LOAD_JOB_COUNT = 3
_STEM_MODE = "6-stems"
_LOCAL_UPLOADS_BUCKET = "clouddsp-uploads"
_RUN_MARKER_PATTERN = re.compile(r"^[a-z0-9]{8,24}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_BYTES = 256 * 1024 * 1024


class ObserverCapabilityContractError(RuntimeError):
    """A safe rejection that intentionally excludes private coordinates."""


@dataclass(frozen=True)
class ExactLoadObjectScope:
    """The one source object and two artifact namespaces for one verified Job.

    The source key is exact because the normal Job API binds it to the
    server-generated ID and fixed filename. Stems and MIDI cannot be exact yet:
    they do not exist until Demucs/Basic Pitch/ADTOF run. A later MinIO policy
    task may use only these three Job-specific prefixes—not a bucket listing or
    an arbitrary prefix supplied by a client.
    """

    job_id: str = field(repr=False)
    bucket: str
    source_object_key: str = field(repr=False)
    source_filename: str
    source_size_bytes: int
    source_sha256: str = field(repr=False)
    stems_prefix: str = field(repr=False)
    midi_prefix: str = field(repr=False)


@dataclass(frozen=True)
class VerifiedLoadObserverCapabilityContract:
    """The minimum immutable input for restricted observer provisioning.

    ``owner_subject`` and every object coordinate remain out of default
    representations so safe broker logs can identify only the opaque run
    marker and the fixed workload shape. The contract carries no password,
    token, generated role name, policy document, queue message, or cleanup
    permission.
    """

    run_marker: str
    owner_subject: str = field(repr=False)
    stem_mode: str
    job_ids: tuple[str, ...] = field(repr=False)
    object_scopes: tuple[ExactLoadObjectScope, ...] = field(repr=False)


def derive_verified_load_observer_capability_contract(
    *, proof: VerifiedOwnerBoundLoadJobs
) -> VerifiedLoadObserverCapabilityContract:
    """Derive exact database/object coordinates from the broker's verified proof.

    Revalidating the fixed shape is intentional defence in depth. The prior
    verifier already checked the live owner-bound Job snapshots, while this
    adapter makes the broker's capability-minting implementation fail closed if a
    refactor accidentally constructs a malformed proof object in memory.
    """

    if not isinstance(proof, VerifiedOwnerBoundLoadJobs):
        raise ObserverCapabilityContractError("observer capability input was not an ownership proof")
    coordinates = proof.coordinates
    _validate_verified_coordinates(proof_subject=proof.subject, coordinates=coordinates)

    scopes = tuple(
        ExactLoadObjectScope(
            job_id=job.job_id,
            bucket=_LOCAL_UPLOADS_BUCKET,
            source_object_key=f"uploads/{job.job_id}/{job.source_filename}",
            source_filename=job.source_filename,
            source_size_bytes=job.source_size_bytes,
            source_sha256=job.source_sha256,
            stems_prefix=f"stems/{job.job_id}/",
            midi_prefix=f"midi/{job.job_id}/",
        )
        for job in coordinates.jobs
    )
    return VerifiedLoadObserverCapabilityContract(
        run_marker=coordinates.run_marker,
        owner_subject=proof.subject,
        stem_mode=_STEM_MODE,
        job_ids=tuple(job.job_id for job in coordinates.jobs),
        object_scopes=scopes,
    )


def _validate_verified_coordinates(
    *, proof_subject: object, coordinates: object
) -> None:
    """Keep capability derivation fixed to the reviewed three-job local contract."""

    if not isinstance(coordinates, AuthenticatedLoadCoordinates):
        raise ObserverCapabilityContractError("ownership proof coordinates were invalid")
    if not isinstance(coordinates.run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(
        coordinates.run_marker
    ):
        raise ObserverCapabilityContractError("ownership proof run marker was invalid")
    if coordinates.stem_mode != _STEM_MODE:
        raise ObserverCapabilityContractError("ownership proof stem mode was invalid")
    _canonical_uuid(proof_subject, purpose="ownership proof subject")
    if coordinates.subject != proof_subject:
        raise ObserverCapabilityContractError("ownership proof subject did not match coordinates")
    if not isinstance(coordinates.jobs, tuple) or len(coordinates.jobs) != _LOAD_JOB_COUNT:
        raise ObserverCapabilityContractError("ownership proof Job count was invalid")

    job_ids: list[str] = []
    for ordinal, job in enumerate(coordinates.jobs, start=1):
        if not isinstance(job, SubmittedLoadJobCoordinate) or job.ordinal != ordinal:
            raise ObserverCapabilityContractError("ownership proof Job ordinal was invalid")
        job_id = _canonical_uuid(job.job_id, purpose="ownership proof Job identifier")
        expected_filename = f"six-stem-load-{coordinates.run_marker}-{ordinal}.wav"
        if job.source_filename != expected_filename:
            raise ObserverCapabilityContractError("ownership proof source filename was invalid")
        if (
            isinstance(job.source_size_bytes, bool)
            or not isinstance(job.source_size_bytes, int)
            or not 1 <= job.source_size_bytes <= _MAX_SOURCE_BYTES
        ):
            raise ObserverCapabilityContractError("ownership proof source size was invalid")
        if not isinstance(job.source_sha256, str) or not _SHA256_PATTERN.fullmatch(job.source_sha256):
            raise ObserverCapabilityContractError("ownership proof source checksum was invalid")
        job_ids.append(job_id)
    if len(set(job_ids)) != _LOAD_JOB_COUNT:
        raise ObserverCapabilityContractError("ownership proof Job identifiers were duplicated")


def _canonical_uuid(value: object, *, purpose: str) -> str:
    """Reject alternate UUID text without copying the received value into errors."""

    if not isinstance(value, str):
        raise ObserverCapabilityContractError(f"{purpose} was invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ObserverCapabilityContractError(f"{purpose} was invalid") from error
    if canonical != value:
        raise ObserverCapabilityContractError(f"{purpose} was invalid")
    return canonical
