"""Drive one ADTOF worker-smoke state transition through injected narrow adapters.

This is a source-only composition facade, not the executable Kubernetes smoke
client. It owns no Boto3/Psycopg/Pika client, network connection, clock,
sleep/retry loop, Secret, image, or Kubernetes resource. A later entrypoint
must construct the restricted clients, use a monotonic clock between calls, and
call :meth:`advance_one` once for each completed external operation.

The facade exists so the eventual entrypoint cannot rearrange the independently
reviewed input, database, output, cleanup, and state-machine boundaries. It
performs exactly the action named by the state machine, then replaces its state
only with the typed result from that action. If an injected adapter raises, the
facade records the state's safe terminal failure category but never exposes the
underlying driver/endpoint diagnostic in its own public error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from adtof_worker_smoke_contract import (
    DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
    ADTOFWorkerSmokeDatabase,
    ADTOFWorkerSmokeObservation,
    FixedObjectEvidence,
    PreparedADTOFWorkerSmoke,
    VerifiedADTOFWorkerSmokeOutputs,
)
from adtof_worker_smoke_fixture import ControlledDrumWav, build_controlled_drum_fixture
from adtof_worker_smoke_orchestration import (
    ADTOFWorkerSmokeAction,
    ADTOFWorkerSmokeOrchestrationError,
    ADTOFWorkerSmokeStateMachine,
)


class ADTOFWorkerSmokeInputBoundary(Protocol):
    """The existing input adapter's two fixed-key operations, and no generic S3 API."""

    def assert_fixed_objects_absent(self) -> None:
        """Refuse a dirty input/MIDI/tempo coordinate before the controlled upload."""

    def upload_controlled_drum_wav(self, fixture: ControlledDrumWav) -> FixedObjectEvidence:
        """Upload and prove only the deterministic fixed drums WAV."""


class ADTOFWorkerSmokeOutputBoundary(Protocol):
    """The existing output adapter's one read-only fixed-result operation."""

    def read_verified_adtof_outputs(
        self, *, task_id: str, input_stem: FixedObjectEvidence
    ) -> VerifiedADTOFWorkerSmokeOutputs:
        """Read and verify only the two ADTOF-owned output keys."""


class ADTOFWorkerSmokeObjectCleanupBoundary(Protocol):
    """The existing cleanup adapter's successful-only exact-object operation."""

    def delete_fixed_objects_after_success(
        self,
        *,
        observation: ADTOFWorkerSmokeObservation,
        input_stem: FixedObjectEvidence,
        outputs: VerifiedADTOFWorkerSmokeOutputs,
    ) -> None:
        """Delete only the fixed evidence after completed first-attempt success."""


class ADTOFWorkerSmokeCompositionError(RuntimeError):
    """Report a failed injected boundary without exposing its concrete diagnostic."""


@dataclass(frozen=True)
class ADTOFWorkerSmokeCompositionStep:
    """One completed source-controlled action and the state it produced.

    This value deliberately contains no media bytes, credentials, S3 response,
    database row, queue message, or underlying exception. The caller can use
    `action`/`state` for concise progress reporting while all detailed adapter
    errors remain in its private exception chain or implementation diagnostics.
    """

    action: ADTOFWorkerSmokeAction
    state: ADTOFWorkerSmokeStateMachine


class ADTOFWorkerSmokeCompositionFacade:
    """Coordinate existing fixed-scope boundaries one action at a time.

    Construction only validates that the supplied objects expose the narrow
    existing methods. It neither creates a connection nor calls an adapter.
    The mutable private state is intentional here: unlike the pure state
    machine, this facade is the future runtime's one owner for an in-progress
    smoke attempt. It never exposes a setter or a generic client capability.
    """

    def __init__(
        self,
        *,
        input_boundary: ADTOFWorkerSmokeInputBoundary,
        database: ADTOFWorkerSmokeDatabase,
        output_boundary: ADTOFWorkerSmokeOutputBoundary,
        object_cleanup: ADTOFWorkerSmokeObjectCleanupBoundary,
        observation_timeout_seconds: int = DEFAULT_OBSERVATION_TIMEOUT_SECONDS,
    ) -> None:
        """Retain preconstructed restricted boundaries and create pure preflight state."""

        self._require_methods(
            input_boundary,
            ("assert_fixed_objects_absent", "upload_controlled_drum_wav"),
            boundary_name="input boundary",
        )
        self._require_methods(
            database,
            ("prepare_fixed_event", "observe_fixed_event", "cleanup_fixed_success"),
            boundary_name="database boundary",
        )
        self._require_methods(
            output_boundary,
            ("read_verified_adtof_outputs",),
            boundary_name="output boundary",
        )
        self._require_methods(
            object_cleanup,
            ("delete_fixed_objects_after_success",),
            boundary_name="object cleanup boundary",
        )
        self._input_boundary = input_boundary
        self._database = database
        self._output_boundary = output_boundary
        self._object_cleanup = object_cleanup
        self._state = ADTOFWorkerSmokeStateMachine.start(
            observation_timeout_seconds=observation_timeout_seconds
        )

    @staticmethod
    def _require_methods(value: object, methods: tuple[str, ...], *, boundary_name: str) -> None:
        """Reject a malformed injected boundary before its first state transition."""

        if any(not callable(getattr(value, method_name, None)) for method_name in methods):
            raise TypeError(f"{boundary_name} does not expose the required fixed operations.")

    @property
    def state(self) -> ADTOFWorkerSmokeStateMachine:
        """Return the immutable current state without exposing a mutable transition API."""

        return self._state

    def advance_one(
        self, *, elapsed_observation_seconds: int | None = None
    ) -> ADTOFWorkerSmokeCompositionStep:
        """Perform precisely the state's next adapter action.

        The future outer loop supplies elapsed *monotonic* seconds only when
        the current action is durable observation. This method does not call a
        clock or wait/sleep. Passing elapsed time for another action, omitting
        it for observation, or advancing a terminal state is a caller error and
        does not reclassify the stored state as an infrastructure failure.
        """

        action = self._state.next_action
        if action is None:
            raise ADTOFWorkerSmokeCompositionError("ADTOF smoke attempt is already terminal.")
        if action is ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT:
            if type(elapsed_observation_seconds) is not int:
                raise ADTOFWorkerSmokeCompositionError(
                    "ADTOF smoke observation requires elapsed monotonic seconds."
                )
        elif elapsed_observation_seconds is not None:
            raise ADTOFWorkerSmokeCompositionError(
                "Elapsed observation seconds are valid only while observing the worker."
            )

        try:
            if action is ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT:
                self._input_boundary.assert_fixed_objects_absent()
                next_state = self._state.preflight_succeeded()
            elif action is ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV:
                # Fixture construction is deterministic and in-memory. The
                # input adapter still recomputes its byte evidence before it
                # performs the only permitted object write.
                evidence = self._input_boundary.upload_controlled_drum_wav(
                    build_controlled_drum_fixture()
                )
                next_state = self._state.input_uploaded(evidence)
            elif action is ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT:
                input_stem = self._required_input_stem()
                prepared = self._database.prepare_fixed_event(
                    stem_size_bytes=input_stem.size_bytes,
                    stem_sha256=input_stem.sha256,
                )
                next_state = self._state.event_prepared(prepared)
            elif action is ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT:
                observation = self._database.observe_fixed_event()
                next_state = self._state.observation_received(
                    observation,
                    elapsed_seconds=elapsed_observation_seconds,
                )
            elif action is ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS:
                observation = self._required_successful_observation()
                input_stem = self._required_input_stem()
                # `is_successful_first_attempt` guarantees this is non-null,
                # but retain an explicit check at the I/O composition boundary.
                if observation.task_id is None:
                    raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke task ID is unavailable.")
                outputs = self._output_boundary.read_verified_adtof_outputs(
                    task_id=observation.task_id,
                    input_stem=input_stem,
                )
                next_state = self._state.outputs_verified(outputs)
            elif action is ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS:
                self._object_cleanup.delete_fixed_objects_after_success(
                    observation=self._required_successful_observation(),
                    input_stem=self._required_input_stem(),
                    outputs=self._required_verified_outputs(),
                )
                next_state = self._state.objects_cleaned()
            elif action is ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT:
                next_state = self._state.database_cleanup_finished(
                    deleted=self._database.cleanup_fixed_success()
                )
            else:  # StrEnum cases are exhaustive; fail closed if a future action is added.
                raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke action is invalid.")
        except Exception as error:
            # Adapter exceptions and invalid typed results share a single safe
            # public category. The state machine classifies the stage truthfully
            # (evidence-preserving vs. partial cleanup), while the exception
            # chain remains available to a future private runtime diagnostic.
            self._state = self._state.current_action_failed()
            raise ADTOFWorkerSmokeCompositionError("ADTOF smoke action failed.") from error

        self._state = next_state
        return ADTOFWorkerSmokeCompositionStep(action=action, state=next_state)

    def _required_input_stem(self) -> FixedObjectEvidence:
        """Read the previously verified WAV fact only after its state transition."""

        if not isinstance(self._state.input_stem, FixedObjectEvidence):
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke input evidence is unavailable.")
        return self._state.input_stem

    def _required_successful_observation(self) -> ADTOFWorkerSmokeObservation:
        """Read the one completed worker fact before output read or cleanup can run."""

        observation = self._state.successful_observation
        if not isinstance(observation, ADTOFWorkerSmokeObservation) or not observation.is_successful_first_attempt:
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke worker success is unavailable.")
        return observation

    def _required_verified_outputs(self) -> VerifiedADTOFWorkerSmokeOutputs:
        """Read the two fixed output proofs only after the output-verifier transition."""

        if not isinstance(self._state.verified_outputs, VerifiedADTOFWorkerSmokeOutputs):
            raise ADTOFWorkerSmokeOrchestrationError("ADTOF smoke output evidence is unavailable.")
        return self._state.verified_outputs
