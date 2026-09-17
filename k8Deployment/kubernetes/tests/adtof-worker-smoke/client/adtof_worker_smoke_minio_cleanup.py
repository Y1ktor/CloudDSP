"""Delete only verified fixed ADTOF smoke objects after successful worker work.

This source-only adapter accepts an injected S3-shaped client.  It imports no
Boto3, constructs no network client, and has no PostgreSQL, RabbitMQ, ADTOF,
Docker, Kubernetes, bucket-list, generic-object, or output-write capability.
It is intentionally a narrow last storage operation, not an end-to-end smoke
orchestrator.

S3 and PostgreSQL do not share a cross-service transaction.  This adapter
therefore makes no claim that deleting these objects also deletes the durable
smoke Job/event; the separate pure orchestration state machine records the
reviewed object-first, guarded-database-second cleanup order. A deletion
failure stops immediately and is reported as a bounded error rather than
silently retrying or deleting more evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from adtof_worker_smoke_contract import (
    ADTOFWorkerSmokeContractError,
    ADTOFWorkerSmokeObservation,
    FixedObjectEvidence,
    VerifiedADTOFWorkerSmokeOutputs,
    fixed_storage_bucket,
)
from adtof_worker_smoke_fixture import MIDI_CONTENT_TYPE, MIDI_KEY, STEM_KEY, TEMPO_CONTENT_TYPE, TEMPO_KEY


# Delete result artifacts before the controlled source WAV.  If an S3 failure
# interrupts this ordered sequence, the bounded input evidence remains for
# diagnosis instead of removing the only reproducible test stimulus first.
_FIXED_CLEANUP_ORDER = (TEMPO_KEY, MIDI_KEY, STEM_KEY)
_MAX_STEM_BYTES = 1 * 1024 * 1024
_MAX_MIDI_BYTES = 16 * 1024 * 1024
_MAX_TEMPO_BYTES = 64 * 1024


class ADTOFWorkerSmokeCleanupClient(Protocol):
    """The one S3 operation the cleanup boundary is allowed to call."""

    def delete_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        """Delete one exact private object coordinate."""


class ADTOFWorkerSmokeCleanupEvidenceError(RuntimeError):
    """Reject an incomplete, failed, or forged success proof before any deletion."""


class ADTOFWorkerSmokeCleanupInfrastructureError(RuntimeError):
    """Hide SDK/endpoint diagnostics when a fixed-object deletion is unavailable."""


def _valid_evidence(
    value: object,
    *,
    key: str,
    content_type: str,
    maximum_size_bytes: int,
) -> FixedObjectEvidence:
    """Repeat fixed key/type/size checks before an evidence object authorizes deletion."""

    if (
        not isinstance(value, FixedObjectEvidence)
        or value.key != key
        or value.content_type != content_type
        or not 1 <= value.size_bytes <= maximum_size_bytes
    ):
        raise ADTOFWorkerSmokeCleanupEvidenceError("ADTOF smoke cleanup evidence is invalid.")
    return value


def _validated_success_proof(
    *,
    observation: object,
    input_stem: object,
    outputs: object,
) -> None:
    """Require independent durable/storage proof before the first S3 mutation.

    The completion predicate is deliberately the same one the future polling
    layer observes: published event, one succeeded first attempt, cleared
    lease, and a Job still in `midi_processing`.  The typed output pair is the
    result of the preceding read/validation adapter; this method only verifies
    it still names the exact two worker-owned result keys.
    """

    if not isinstance(observation, ADTOFWorkerSmokeObservation) or not observation.is_successful_first_attempt:
        raise ADTOFWorkerSmokeCleanupEvidenceError("ADTOF smoke worker success is not verified.")
    _valid_evidence(
        input_stem,
        key=STEM_KEY,
        content_type="audio/wav",
        maximum_size_bytes=_MAX_STEM_BYTES,
    )
    if not isinstance(outputs, VerifiedADTOFWorkerSmokeOutputs):
        raise ADTOFWorkerSmokeCleanupEvidenceError("ADTOF smoke cleanup evidence is invalid.")
    _valid_evidence(
        outputs.midi,
        key=MIDI_KEY,
        content_type=MIDI_CONTENT_TYPE,
        maximum_size_bytes=_MAX_MIDI_BYTES,
    )
    _valid_evidence(
        outputs.tempo,
        key=TEMPO_KEY,
        content_type=TEMPO_CONTENT_TYPE,
        maximum_size_bytes=_MAX_TEMPO_BYTES,
    )


class SuccessfulOnlyADTOFWorkerSmokeMinIOCleanupAdapter:
    """Delete exactly three fixed objects after fully verified worker success.

    Public callers cannot choose a bucket, key, prefix, object body, or retry
    count. The method makes exactly three calls in a documented order. It does
    not inspect the result as an authorization decision: a successful S3 call
    is sufficient, while an exception stops subsequent deletes so an operator
    can inspect remaining evidence rather than this adapter guessing recovery.
    """

    def __init__(self, client: ADTOFWorkerSmokeCleanupClient) -> None:
        """Store an injected restricted client without creating a network connection."""

        if not hasattr(client, "delete_object"):
            raise TypeError("client must expose the fixed smoke cleanup operation.")
        self._client = client

    def _delete_fixed_key(self, key: str) -> None:
        """Delete one source-controlled key and redact concrete S3 failure details."""

        if key not in _FIXED_CLEANUP_ORDER:
            raise ADTOFWorkerSmokeContractError("ADTOF smoke cleanup key is invalid.")
        try:
            response = self._client.delete_object(Bucket=fixed_storage_bucket(), Key=key)
        except Exception as error:  # Vendor exception types stay outside this portable adapter.
            raise ADTOFWorkerSmokeCleanupInfrastructureError(
                "ADTOF smoke object cleanup is unavailable."
            ) from error
        if not isinstance(response, Mapping):
            raise ADTOFWorkerSmokeCleanupInfrastructureError(
                "ADTOF smoke object cleanup is unavailable."
            )

    def delete_fixed_objects_after_success(
        self,
        *,
        observation: ADTOFWorkerSmokeObservation,
        input_stem: FixedObjectEvidence,
        outputs: VerifiedADTOFWorkerSmokeOutputs,
    ) -> None:
        """Delete fixed tempo, MIDI, then input evidence after verified success only.

        The arguments are success proofs, not configuration. They are validated
        fully before the first deletion, so an invalid observation or forged
        evidence cannot cause a partial destructive action. A later client
        orchestrator remains responsible for the separate guarded database
        cleanup and for operator-visible handling of any partial S3 cleanup.
        """

        _validated_success_proof(
            observation=observation,
            input_stem=input_stem,
            outputs=outputs,
        )
        for key in _FIXED_CLEANUP_ORDER:
            self._delete_fixed_key(key)
