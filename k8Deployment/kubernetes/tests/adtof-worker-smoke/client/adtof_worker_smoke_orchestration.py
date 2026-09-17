"""Model the fixed ADTOF worker-smoke workflow without performing I/O.

This module is intentionally a pure state machine, not a Kubernetes controller
or a runtime smoke client.  It does not construct a database/S3/RabbitMQ/model
client, read a clock, sleep, open a socket, create a Job, or mutate a resource.
A later composition root will perform one source-controlled action at a time,
then feed only its typed outcome back into this state machine.

Keeping the sequence explicit is important because the smoke test crosses
MinIO, PostgreSQL, the generic dispatcher, RabbitMQ, and the deployed ADTOF
worker without a distributed transaction.  The state machine makes the safe
order reviewable and distinguishes failures that preserve all evidence from
cleanup failures that may have removed some fixed objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from adtof_worker_smoke_contract import (
    DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
    MAX_OBSERVATION_TIMEOUT_SECONDS,
    MIN_OBSERVATION_TIMEOUT_SECONDS,
    ADTOFWorkerSmokeObservation,
    FixedObjectEvidence,
    PreparedADTOFWorkerSmoke,
    VerifiedADTOFWorkerSmokeOutputs,
)
from adtof_worker_smoke_fixture import MIDI_CONTENT_TYPE, MIDI_KEY, STEM_KEY, TEMPO_CONTENT_TYPE, TEMPO_KEY


class ADTOFWorkerSmokePhase(StrEnum):
    """Every legal state in the future one-shot ADTOF worker smoke workflow."""

    PREFLIGHT = "preflight"
    UPLOAD_INPUT = "upload_input"
    PREPARE_DURABLE_EVENT = "prepare_durable_event"
    OBSERVE_WORKER = "observe_worker"
    VERIFY_OUTPUTS = "verify_outputs"
    CLEANUP_OBJECTS = "cleanup_objects"
    CLEANUP_DATABASE = "cleanup_database"
    PASSED = "passed"
    TIMED_OUT_PRESERVING_EVIDENCE = "timed_out_preserving_evidence"
    FAILED_PRESERVING_EVIDENCE = "failed_preserving_evidence"
    OBJECT_CLEANUP_INCOMPLETE = "object_cleanup_incomplete"
    DATABASE_CLEANUP_INCOMPLETE = "database_cleanup_incomplete"


class ADTOFWorkerSmokeAction(StrEnum):
    """One narrow adapter operation a later composition may perform next."""

    ASSERT_FIXED_OBJECTS_ABSENT = "assert_fixed_objects_absent"
    UPLOAD_CONTROLLED_WAV = "upload_controlled_wav"
    PREPARE_FIXED_EVENT = "prepare_fixed_event"
    OBSERVE_FIXED_EVENT = "observe_fixed_event"
    READ_VERIFY_OUTPUTS = "read_verify_outputs"
    DELETE_FIXED_OBJECTS = "delete_fixed_objects"
    CLEANUP_FIXED_DATABASE_EVENT = "cleanup_fixed_database_event"


class ADTOFWorkerSmokeOutcome(StrEnum):
    """A terminal report category that never includes SDK/database diagnostics."""

    PASSED = "passed"
    TIMED_OUT_PRESERVING_EVIDENCE = "timed_out_preserving_evidence"
    FAILED_PRESERVING_EVIDENCE = "failed_preserving_evidence"
    OBJECT_CLEANUP_INCOMPLETE = "object_cleanup_incomplete"
    DATABASE_CLEANUP_INCOMPLETE = "database_cleanup_incomplete"


class ADTOFWorkerSmokeOrchestrationError(RuntimeError):
    """Reject an out-of-order or malformed pure workflow transition."""


_ACTION_BY_PHASE = {
    ADTOFWorkerSmokePhase.PREFLIGHT: ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT,
    ADTOFWorkerSmokePhase.UPLOAD_INPUT: ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV,
    ADTOFWorkerSmokePhase.PREPARE_DURABLE_EVENT: ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT,
    ADTOFWorkerSmokePhase.OBSERVE_WORKER: ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT,
    ADTOFWorkerSmokePhase.VERIFY_OUTPUTS: ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS,
    ADTOFWorkerSmokePhase.CLEANUP_OBJECTS: ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS,
    ADTOFWorkerSmokePhase.CLEANUP_DATABASE: ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT,
}

_OUTCOME_BY_PHASE = {
    ADTOFWorkerSmokePhase.PASSED: ADTOFWorkerSmokeOutcome.PASSED,
    ADTOFWorkerSmokePhase.TIMED_OUT_PRESERVING_EVIDENCE: ADTOFWorkerSmokeOutcome.TIMED_OUT_PRESERVING_EVIDENCE,
    ADTOFWorkerSmokePhase.FAILED_PRESERVING_EVIDENCE: ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE,
    ADTOFWorkerSmokePhase.OBJECT_CLEANUP_INCOMPLETE: ADTOFWorkerSmokeOutcome.OBJECT_CLEANUP_INCOMPLETE,
    ADTOFWorkerSmokePhase.DATABASE_CLEANUP_INCOMPLETE: ADTOFWorkerSmokeOutcome.DATABASE_CLEANUP_INCOMPLETE,
}

_MAX_STEM_BYTES = 1 * 1024 * 1024
_MAX_MIDI_BYTES = 16 * 1024 * 1024
_MAX_TEMPO_BYTES = 64 * 1024


def _valid_evidence(
    value: object,
    *,
    key: str,
    content_type: str,
    maximum_size_bytes: int,
) -> FixedObjectEvidence:
    """Keep later transitions tied to the exact bounded policy-granted keys."""

    if (
        not isinstance(value, FixedObjectEvidence)
        or value.key != key
        or value.content_type != content_type
        or not 1 <= value.size_bytes <= maximum_size_bytes
    ):
        raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke evidence is invalid.")
    return value


def _valid_outputs(value: object) -> VerifiedADTOFWorkerSmokeOutputs:
    """Require the two producer-owned objects before storage cleanup is possible."""

    if not isinstance(value, VerifiedADTOFWorkerSmokeOutputs):
        raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke output evidence is invalid.")
    _valid_evidence(
        value.midi,
        key=MIDI_KEY,
        content_type=MIDI_CONTENT_TYPE,
        maximum_size_bytes=_MAX_MIDI_BYTES,
    )
    _valid_evidence(
        value.tempo,
        key=TEMPO_KEY,
        content_type=TEMPO_CONTENT_TYPE,
        maximum_size_bytes=_MAX_TEMPO_BYTES,
    )
    return value


def _terminal_observation_failure(observation: ADTOFWorkerSmokeObservation) -> bool:
    """Identify facts that can no longer produce the smoke test's first-attempt success.

    A not-yet-published event, absent task, lease, or running task is still
    observable progress. In contrast, a dead-lettered event, retry, second
    attempt, failed task/job, or a malformed would-be completion cannot become
    the required first-attempt `midi_processing` result by waiting longer.
    """

    if observation.publication_status == "dead_lettered" or observation.job_status in {"failed", "completed"}:
        return True
    if observation.task_id is None:
        return False
    if observation.task_status in {"failed", "retry_scheduled"}:
        return True
    if observation.task_attempt_count is not None and observation.task_attempt_count > 1:
        return True
    # The database function returns one coherent committed task projection. A
    # `succeeded` task that does not satisfy the reviewed end predicate is not
    # a transient polling state; keeping it would only hide a contract drift.
    return observation.task_status == "succeeded"


@dataclass(frozen=True)
class ADTOFWorkerSmokeStateMachine:
    """Immutable workflow state for one fixed-coordinate ADTOF smoke attempt.

    The machine retains typed proof solely to enforce the next pure transition;
    it holds neither bytes, credentials, database rows, URLs, error strings,
    nor a client object. Each method returns a new state, so a later runtime
    can log/report a phase transition without mutating hidden shared state.
    """

    phase: ADTOFWorkerSmokePhase
    observation_timeout_seconds: int
    elapsed_observation_seconds: int = 0
    input_stem: FixedObjectEvidence | None = None
    prepared_event: PreparedADTOFWorkerSmoke | None = None
    successful_observation: ADTOFWorkerSmokeObservation | None = None
    verified_outputs: VerifiedADTOFWorkerSmokeOutputs | None = None

    @classmethod
    def start(
        cls, *, observation_timeout_seconds: int = DEFAULT_OBSERVATION_TIMEOUT_SECONDS
    ) -> "ADTOFWorkerSmokeStateMachine":
        """Create the preflight state with the contract's finite wait bound."""

        if (
            type(observation_timeout_seconds) is not int
            or not MIN_OBSERVATION_TIMEOUT_SECONDS
            <= observation_timeout_seconds
            <= MAX_OBSERVATION_TIMEOUT_SECONDS
        ):
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke observation timeout is invalid.")
        return cls(
            phase=ADTOFWorkerSmokePhase.PREFLIGHT,
            observation_timeout_seconds=observation_timeout_seconds,
        )

    @property
    def next_action(self) -> ADTOFWorkerSmokeAction | None:
        """Return the sole permitted next external operation, or none when terminal."""

        return _ACTION_BY_PHASE.get(self.phase)

    @property
    def outcome(self) -> ADTOFWorkerSmokeOutcome | None:
        """Return a safe terminal category without leaking an underlying error."""

        return _OUTCOME_BY_PHASE.get(self.phase)

    def _require_phase(self, expected: ADTOFWorkerSmokePhase) -> None:
        """Prevent a composition root from skipping a required safety boundary."""

        if self.phase is not expected:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke transition is out of order.")

    def preflight_succeeded(self) -> "ADTOFWorkerSmokeStateMachine":
        """Advance only after all three fixed object keys were proved absent."""

        self._require_phase(ADTOFWorkerSmokePhase.PREFLIGHT)
        return self._replace(phase=ADTOFWorkerSmokePhase.UPLOAD_INPUT)

    def input_uploaded(self, evidence: FixedObjectEvidence) -> "ADTOFWorkerSmokeStateMachine":
        """Advance only after the controlled WAV was put and HeadObject-verified."""

        self._require_phase(ADTOFWorkerSmokePhase.UPLOAD_INPUT)
        return self._replace(
            phase=ADTOFWorkerSmokePhase.PREPARE_DURABLE_EVENT,
            input_stem=_valid_evidence(
                evidence,
                key=STEM_KEY,
                content_type="audio/wav",
                maximum_size_bytes=_MAX_STEM_BYTES,
            ),
        )

    def event_prepared(
        self, prepared: PreparedADTOFWorkerSmoke
    ) -> "ADTOFWorkerSmokeStateMachine":
        """Begin bounded worker observation after atomic Job/outbox creation commits."""

        self._require_phase(ADTOFWorkerSmokePhase.PREPARE_DURABLE_EVENT)
        if not isinstance(prepared, PreparedADTOFWorkerSmoke) or self.input_stem is None:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke prepared event is invalid.")
        return self._replace(
            phase=ADTOFWorkerSmokePhase.OBSERVE_WORKER,
            prepared_event=prepared,
        )

    def observation_received(
        self,
        observation: ADTOFWorkerSmokeObservation | None,
        *,
        elapsed_seconds: int,
    ) -> "ADTOFWorkerSmokeStateMachine":
        """Record one bounded durable observation and choose progress/failure/timeout.

        `elapsed_seconds` must be provided by a later runtime's monotonic-clock
        loop. This pure module deliberately does not sleep or read the current
        time itself. A valid success at the exact deadline is accepted; an
        incomplete observation at that deadline becomes a timeout that leaves
        all objects and durable rows untouched.
        """

        self._require_phase(ADTOFWorkerSmokePhase.OBSERVE_WORKER)
        if type(elapsed_seconds) is not int or elapsed_seconds < self.elapsed_observation_seconds:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke observation elapsed time is invalid.")
        if observation is not None and not isinstance(observation, ADTOFWorkerSmokeObservation):
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke observation is invalid.")
        current = self._replace(elapsed_observation_seconds=elapsed_seconds)
        if observation is not None and observation.is_successful_first_attempt:
            return current._replace(
                phase=ADTOFWorkerSmokePhase.VERIFY_OUTPUTS,
                successful_observation=observation,
            )
        if observation is not None and _terminal_observation_failure(observation):
            return current._replace(phase=ADTOFWorkerSmokePhase.FAILED_PRESERVING_EVIDENCE)
        if elapsed_seconds >= self.observation_timeout_seconds:
            return current._replace(phase=ADTOFWorkerSmokePhase.TIMED_OUT_PRESERVING_EVIDENCE)
        return current

    def outputs_verified(
        self, outputs: VerifiedADTOFWorkerSmokeOutputs
    ) -> "ADTOFWorkerSmokeStateMachine":
        """Permit object cleanup only after both worker-created outputs were verified."""

        self._require_phase(ADTOFWorkerSmokePhase.VERIFY_OUTPUTS)
        if self.successful_observation is None or self.input_stem is None:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke success proof is invalid.")
        return self._replace(
            phase=ADTOFWorkerSmokePhase.CLEANUP_OBJECTS,
            verified_outputs=_valid_outputs(outputs),
        )

    def objects_cleaned(self) -> "ADTOFWorkerSmokeStateMachine":
        """Allow guarded PostgreSQL cleanup only after all three object deletes returned."""

        self._require_phase(ADTOFWorkerSmokePhase.CLEANUP_OBJECTS)
        if self.successful_observation is None or self.input_stem is None or self.verified_outputs is None:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke cleanup proof is invalid.")
        return self._replace(phase=ADTOFWorkerSmokePhase.CLEANUP_DATABASE)

    def database_cleanup_finished(self, *, deleted: bool) -> "ADTOFWorkerSmokeStateMachine":
        """Mark success only when the guarded database cleanup actually removed its row."""

        self._require_phase(ADTOFWorkerSmokePhase.CLEANUP_DATABASE)
        if type(deleted) is not bool:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke database cleanup result is invalid.")
        return self._replace(
            phase=(
                ADTOFWorkerSmokePhase.PASSED
                if deleted
                else ADTOFWorkerSmokePhase.DATABASE_CLEANUP_INCOMPLETE
            )
        )

    def current_action_failed(self) -> "ADTOFWorkerSmokeStateMachine":
        """Classify a failed future adapter call without inspecting its raw error.

        Before object cleanup, the fixed durable/object evidence is retained for
        diagnosis. An object-cleanup failure may be partial because S3 has no
        multi-object transaction. A database-cleanup failure happens only after
        the successful object-cleanup action, so it receives its own truthful
        terminal category rather than being misreported as evidence-preserving.
        """

        if self.phase in {
            ADTOFWorkerSmokePhase.PREFLIGHT,
            ADTOFWorkerSmokePhase.UPLOAD_INPUT,
            ADTOFWorkerSmokePhase.PREPARE_DURABLE_EVENT,
            ADTOFWorkerSmokePhase.OBSERVE_WORKER,
            ADTOFWorkerSmokePhase.VERIFY_OUTPUTS,
        }:
            return self._replace(phase=ADTOFWorkerSmokePhase.FAILED_PRESERVING_EVIDENCE)
        if self.phase is ADTOFWorkerSmokePhase.CLEANUP_OBJECTS:
            return self._replace(phase=ADTOFWorkerSmokePhase.OBJECT_CLEANUP_INCOMPLETE)
        if self.phase is ADTOFWorkerSmokePhase.CLEANUP_DATABASE:
            return self._replace(phase=ADTOFWorkerSmokePhase.DATABASE_CLEANUP_INCOMPLETE)
        raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke terminal state cannot fail again.")

    def _replace(self, **changes: object) -> "ADTOFWorkerSmokeStateMachine":
        """Return one new state without exposing a mutable transition mechanism."""

        values = {
            "phase": self.phase,
            "observation_timeout_seconds": self.observation_timeout_seconds,
            "elapsed_observation_seconds": self.elapsed_observation_seconds,
            "input_stem": self.input_stem,
            "prepared_event": self.prepared_event,
            "successful_observation": self.successful_observation,
            "verified_outputs": self.verified_outputs,
        }
        values.update(changes)
        return ADTOFWorkerSmokeStateMachine(**values)  # type: ignore[arg-type]
