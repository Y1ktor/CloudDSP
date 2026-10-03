"""Run one already-acknowledged, already-claimed Basic Pitch task in order.

This module is deliberately a *post-claim* coordinator, not an AMQP consumer.
A later RabbitMQ layer must first parse a delivery, commit the durable task
claim, and manually acknowledge the broker only for a committed claim,
duplicate, or stale result.  It may call this function only for the committed
``claimed`` lease.  That future split prevents a transient MinIO/model failure
from being mistaken for a broker acknowledgement decision.

For that one lease, this coordinator keeps the existing narrow boundaries in
the only safe happy-path order:

1. Verify the exact private Demucs stem with MinIO ``HeadObject``.
2. Download/hash the stem and commit ``leased`` -> ``running``.  The temporary
   WAV exists only inside that context.
3. Create and run the fixed shell-free Basic Pitch command.
4. Estimate best-effort BPM evidence from the same verified WAV, then
   validate/hash the Standard MIDI File and build its deterministic MinIO plan.
5. Upload the MIDI, then verify stored-object metadata with ``HeadObject``.
6. Commit the lease-token-guarded ``running`` -> ``succeeded`` task update and
   its per-stem tempo candidate together.

The coordinator imports no Pika, receives no delivery tag, acknowledges or
rejects no message, retries nothing, and changes no Kubernetes resource.  It
also owns no long database transaction: the start and completion helpers each
open their own short transaction, while MinIO and CPU work occur outside them.
Any external dependency or model error propagates unchanged to a later worker
supervisor, which will define the bounded retry/recovery policy separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.processing.basic_pitch_process import (
    DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    BasicPitchProcessRunner,
    build_basic_pitch_inference_command,
    run_basic_pitch_inference,
)
from app.messaging.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.artifacts.midi_artifact import verify_and_hash_basic_pitch_midi
from app.artifacts.midi_artifact_head_object import (
    BasicPitchMidiHeadObjectClient,
    VerifiedStoredBasicPitchMidiObject,
    verify_uploaded_basic_pitch_midi_head_object,
)
from app.artifacts.midi_artifact_upload import (
    BasicPitchMidiPutObjectClient,
    upload_basic_pitch_midi_object,
)
from app.artifacts.midi_output_object import build_basic_pitch_midi_output_object
from app.db.midi_task_completion import BasicPitchMidiTaskCompletion
from app.db.midi_task_completion_commit import (
    BasicPitchMidiTaskCompletionDatabase,
    commit_verified_basic_pitch_midi_task,
)
from app.processing.tempo_candidate import estimate_basic_pitch_tempo_candidate
from app.runtime.stem_retry_handling import BasicPitchPreModelRetryDatabase
from app.artifacts.stem_download import BasicPitchGetObjectClient
from app.artifacts.stem_object import BasicPitchHeadObjectClient, verify_claimed_basic_pitch_stem_head_object
from app.db.stem_task_terminal_failure import BasicPitchStemTerminalFailure
from app.db.stem_task_retry_exhaustion import BasicPitchStemRetryExhaustion
from app.db.stem_task_retry_schedule import BasicPitchStemRetrySchedule
from app.db.stem_task_start import (
    BasicPitchTaskStartDatabase,
    RunningBasicPitchStem,
    started_verified_basic_pitch_stem,
)
from app.db.task_lease import BasicPitchTaskLease


class BasicPitchTaskExecutionStorageClient(
    BasicPitchHeadObjectClient,
    BasicPitchGetObjectClient,
    BasicPitchMidiPutObjectClient,
    BasicPitchMidiHeadObjectClient,
    Protocol,
):
    """The exact MinIO surface used after a lease has been committed.

    The permanent Basic Pitch MinIO policy, not this Protocol, authorizes the
    underlying calls.  Combining the already-narrow operation Protocols here
    makes the coordinator's capability explicit: it may head/download a
    claimed ``stems/*`` object and put/head its deterministic ``midi/*``
    object, but it cannot list or delete bucket contents.
    """


class BasicPitchTaskExecutionDatabase(
    BasicPitchTaskStartDatabase,
    BasicPitchMidiTaskCompletionDatabase,
    BasicPitchPreModelRetryDatabase,
    Protocol,
):
    """One restricted database provider for the task's short transactions.

    ``PsycopgBasicPitchDatabase`` already implements the shared
    ``write_cursor()`` shape. The post-ack gate gives it to a pre-model
    terminal/retry transition only after a caught reviewed error; the
    coordinator gives it to start and completion helpers. It never opens a
    cursor around MinIO or model work.
    """


class BasicPitchClaimedTaskExecutionOutcome(StrEnum):
    """The normal, non-exception outcomes after one committed lease.

    Only a committed task result may become an outcome. A reviewed transient
    *pre-model* storage failure now has two explicit results: a durable retry
    schedule on attempts one/two, or a terminal exhausted-retry record on
    attempt three. Storage-protocol, database, model, and post-model output
    errors intentionally remain exceptions, so the later supervisor cannot
    mistake incomplete work for a successful broker or PostgreSQL decision.
    """

    SUCCEEDED = "succeeded"
    # A permanent stem-integrity failure was durably recorded before model
    # start. It is terminal for this task, not a successful MIDI extraction.
    TERMINAL_FAILURE = "terminal_failure"
    # A temporary pre-model storage failure has a committed later retry time.
    RETRY_SCHEDULED = "retry_scheduled"
    # The same temporary failure occurred on the final permitted attempt.
    RETRY_EXHAUSTED = "retry_exhausted"
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True)
class BasicPitchClaimedTaskExecution:
    """Non-sensitive result of one post-claim execution attempt.

    A success carries only the completion evidence returned after the final
    short transaction commits. A permanent pre-model failure and a
    final-attempt storage failure each carry their own committed terminal
    evidence. A first/second temporary storage failure carries the committed
    later retry time. ``OWNERSHIP_LOST`` means a start, completion, or failure
    token guard returned no row; no further work should be attempted by this
    owner. It includes no object key, local path, credential, or raw backend
    response, keeping it safe for a future metrics boundary.
    """

    outcome: BasicPitchClaimedTaskExecutionOutcome
    completion: BasicPitchMidiTaskCompletion | None = None
    terminal_failure: BasicPitchStemTerminalFailure | None = None
    retry_schedule: BasicPitchStemRetrySchedule | None = None
    retry_exhaustion: BasicPitchStemRetryExhaustion | None = None

    def __post_init__(self) -> None:
        """Keep the public outcome/result pairing unambiguous for callers."""

        if self.outcome is BasicPitchClaimedTaskExecutionOutcome.SUCCEEDED:
            if (
                not isinstance(self.completion, BasicPitchMidiTaskCompletion)
                or self.terminal_failure is not None
                or self.retry_schedule is not None
                or self.retry_exhaustion is not None
            ):
                raise TypeError("A successful Basic Pitch task execution requires completion evidence.")
            return
        if self.outcome is BasicPitchClaimedTaskExecutionOutcome.TERMINAL_FAILURE:
            if (
                self.completion is not None
                or not isinstance(self.terminal_failure, BasicPitchStemTerminalFailure)
                or self.retry_schedule is not None
                or self.retry_exhaustion is not None
            ):
                raise TypeError("A terminal Basic Pitch task failure requires failure evidence.")
            return
        if self.outcome is BasicPitchClaimedTaskExecutionOutcome.RETRY_SCHEDULED:
            if (
                self.completion is not None
                or self.terminal_failure is not None
                or not isinstance(self.retry_schedule, BasicPitchStemRetrySchedule)
                or self.retry_exhaustion is not None
            ):
                raise TypeError("A scheduled Basic Pitch retry requires retry evidence.")
            return
        if self.outcome is BasicPitchClaimedTaskExecutionOutcome.RETRY_EXHAUSTED:
            if (
                self.completion is not None
                or self.terminal_failure is not None
                or self.retry_schedule is not None
                or not isinstance(self.retry_exhaustion, BasicPitchStemRetryExhaustion)
            ):
                raise TypeError("An exhausted Basic Pitch retry requires terminal evidence.")
            return
        if self.outcome is BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST:
            if (
                self.completion is not None
                or self.terminal_failure is not None
                or self.retry_schedule is not None
                or self.retry_exhaustion is not None
            ):
                raise TypeError("A lost Basic Pitch task lease cannot include completion evidence.")
            return
        raise TypeError("Basic Pitch task execution outcome is invalid.")


def _validated_timeout_seconds(value: object) -> int:
    """Delegate the reviewed timeout bounds to the fixed process boundary.

    Calling the process helper is what ultimately validates this value before a
    child process exists.  This early exact-type check prevents a boolean or
    another surprising value from crossing the orchestration interface first.
    """

    if type(value) is not int:
        raise TypeError("process_timeout_seconds must be an integer.")
    return value


def execute_claimed_basic_pitch_task(
    *,
    database: BasicPitchTaskExecutionDatabase,
    storage_client: BasicPitchTaskExecutionStorageClient,
    message: BasicPitchRequestedMessage,
    lease: BasicPitchTaskLease,
    work_directory: Path,
    process_timeout_seconds: int = DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    process_runner: BasicPitchProcessRunner | None = None,
) -> BasicPitchClaimedTaskExecution:
    """Execute exactly one committed Basic Pitch lease through durable success.

    ``message`` and ``lease`` must come from the same committed first-claim
    decision.  Each downstream boundary revalidates their strict identities
    before it performs I/O, so a hand-built frozen dataclass cannot widen a
    bucket/key/stem choice.  A future AMQP consumer is responsible for proving
    that prerequisite; this function deliberately cannot parse or acknowledge
    an AMQP delivery.

    The ``started_verified_basic_pitch_stem`` context owns the temporary WAV
    directory.  Every model/output/storage operation stays *inside* that
    context, ensuring the output plan's local MIDI path remains valid through
    upload and stored-object verification.  A normal ``None`` at either guarded
    database transition is ownership loss, not success: the coordinator stops
    and performs no later action.  Exceptions are not caught or classified
    here, preserving a later supervisor's ability to apply a reviewed retry or
    terminal-failure policy without hiding an incomplete operation.
    """

    if not isinstance(message, BasicPitchRequestedMessage):
        raise TypeError("message must be BasicPitchRequestedMessage.")
    if not isinstance(lease, BasicPitchTaskLease):
        raise TypeError("lease must be BasicPitchTaskLease.")
    if not isinstance(work_directory, Path):
        raise TypeError("work_directory must be pathlib.Path.")
    if not callable(getattr(database, "write_cursor", None)):
        raise TypeError("database must provide write_cursor.")
    for method_name in ("head_object", "get_object", "put_object"):
        if not callable(getattr(storage_client, method_name, None)):
            raise TypeError("storage_client does not provide the required MinIO operations.")
    timeout_seconds = _validated_timeout_seconds(process_timeout_seconds)

    # A metadata-only stem check happens before downloading bytes and before
    # the `leased` -> `running` transition.  A permanent mismatch will later
    # receive its own result policy; it must never make the model start.
    verified_stem = verify_claimed_basic_pitch_stem_head_object(
        storage_client,
        lease=lease,
        message=message,
    )

    # This context downloads/hash-verifies the private WAV and commits the
    # start transition before yielding. Its exit removes the WAV and the fresh
    # Basic Pitch output directory, so all output work must occur inside it.
    with started_verified_basic_pitch_stem(
        database=database,
        client=storage_client,
        lease=lease,
        source=verified_stem,
        work_directory=work_directory,
    ) as running:
        if running is None:
            return BasicPitchClaimedTaskExecution(
                outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
            )
        if not isinstance(running, RunningBasicPitchStem):
            raise TypeError("Basic Pitch task start returned an invalid running stem.")

        inference = build_basic_pitch_inference_command(
            running=running,
            work_directory=work_directory,
        )
        completed_inference = run_basic_pitch_inference(
            inference,
            timeout_seconds=timeout_seconds,
            runner=process_runner,
        )
        # The cloud workflow estimates BPM from each input stem with librosa
        # after MIDI extraction. Keep this as best-effort evidence: a tempo
        # analysis problem must not invalidate the correctly generated MIDI.
        tempo_candidate = estimate_basic_pitch_tempo_candidate(running.stem.stem_path)
        artifact = verify_and_hash_basic_pitch_midi(completed_inference)
        output_object = build_basic_pitch_midi_output_object(
            lease=lease,
            message=message,
            artifact=artifact,
        )
        upload_receipt = upload_basic_pitch_midi_object(
            client=storage_client,
            output_object=output_object,
        )
        stored_midi = verify_uploaded_basic_pitch_midi_head_object(
            storage_client,
            output_object=output_object,
            upload_receipt=upload_receipt,
        )
        completion = commit_verified_basic_pitch_midi_task(
            database=database,
            lease=lease,
            stored_midi=stored_midi,
            tempo_candidate=tempo_candidate,
        )

    # A completion no-row result means a recovery/expiry/state change won
    # between the MinIO proof and the final token-guarded transaction. The
    # uploaded object remains deterministic and provenance-tagged, but this
    # worker must not call it a completed task or make a broker decision.
    if completion is None:
        return BasicPitchClaimedTaskExecution(
            outcome=BasicPitchClaimedTaskExecutionOutcome.OWNERSHIP_LOST,
        )
    return BasicPitchClaimedTaskExecution(
        outcome=BasicPitchClaimedTaskExecutionOutcome.SUCCEEDED,
        completion=completion,
    )
