"""Compose the fixed adapters into one safe three-request KEDA burst sequence.

This module is intentionally still SDK-free. A later entrypoint/factory task
will construct the real Boto3 and Psycopg clients from ``BurstSettings`` and
pass their narrow adapters here. Keeping sequencing separate from client
construction makes its failure behavior testable without a cluster:

``preflight -> upload -> durable prepare -> wait -> verify -> cleanup``

After ``prepare`` starts, this runner never performs automatic cleanup on an
error or timeout. The three durable records and six exact object coordinates
remain available for diagnosis. Only the pre-durable upload failure path may
attempt best-effort cleanup because database evidence cannot exist yet.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from basic_pitch_keda_burst_smoke import (
    BURST_COORDINATES,
    COMPLETION_TIMEOUT_SECONDS,
    BasicPitchKedaBurstContractError,
    ControlledWav,
    build_all_controlled_wavs,
)
from minio_adapter import VerifiedMidi
from postgresql_adapter import BurstTaskObservation


POLL_INTERVAL_SECONDS = 1.0


class DatabaseBurstOperations(Protocol):
    """The three fixed database operations the composition layer may request."""

    def assert_coordinates_clean(self) -> None: ...

    def prepare_durable_burst(self, wavs: tuple[ControlledWav, ...]) -> None: ...

    def read_observations(self) -> tuple[BurstTaskObservation, ...]: ...

    def cleanup_successful_burst(self) -> None: ...


class MinioBurstOperations(Protocol):
    """The exact-key object operations the composition layer may request."""

    def assert_all_coordinates_absent(self) -> None: ...

    def upload_controlled_wavs(self, wavs: tuple[ControlledWav, ...]) -> None: ...

    def verify_midi(
        self,
        *,
        coordinate: object,
        task_id: str,
        input_sha256: str,
    ) -> VerifiedMidi: ...

    def delete_all_fixed_objects(self) -> None: ...


def _completed_observations(
    observations: tuple[BurstTaskObservation, ...],
) -> tuple[BurstTaskObservation, ...] | None:
    """Return three normal first-attempt completions in fixed coordinate order.

    A RabbitMQ queue drain alone would not prove durable processing. This
    decision uses only the PostgreSQL function projection: publication is
    recorded, each worker task succeeded on attempt one, its lease is clear,
    and the Job retains the reviewed ``midi_processing`` state required by the
    database cleanup function.
    """

    by_label = {observation.request_label: observation for observation in observations}
    expected_labels = tuple(coordinate.label for coordinate in BURST_COORDINATES)
    if set(by_label) != set(expected_labels) or len(by_label) != len(observations):
        raise BasicPitchKedaBurstContractError("Burst database observation lost a fixed request coordinate.")
    ordered = tuple(by_label[label] for label in expected_labels)
    if all(
        observation.publication_status == "published"
        and observation.published_at is not None
        and observation.task_id is not None
        and observation.task_status == "succeeded"
        and observation.task_attempt_count == 1
        and observation.task_lease_is_clear
        and observation.task_completed_at is not None
        and observation.job_status == "midi_processing"
        for observation in ordered
    ):
        return ordered
    return None


def wait_for_worker_completions(
    database: DatabaseBurstOperations,
    *,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[BurstTaskObservation, ...]:
    """Poll durable facts for at most the fixed 300-second completion window."""

    deadline = monotonic() + COMPLETION_TIMEOUT_SECONDS
    while True:
        completed = _completed_observations(database.read_observations())
        if completed is not None:
            return completed
        if monotonic() >= deadline:
            raise BasicPitchKedaBurstContractError("Timed out waiting for the three Basic Pitch worker completions.")
        sleep_function(POLL_INTERVAL_SECONDS)


def run_burst(
    database: DatabaseBurstOperations,
    storage: MinioBurstOperations,
    *,
    report: Callable[[str], None] = print,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[VerifiedMidi, ...]:
    """Run the smoke's only permitted normal-path sequence once.

    The client does not publish/consume RabbitMQ messages, read an HPA, set a
    replica count, create a Kubernetes Job, or invoke an ML model. The deployed
    generic dispatcher, KEDA scaler, and worker Deployment do those jobs after
    the database `prepare` function commits its three normal outbox events.
    """

    report("Basic Pitch KEDA burst smoke: checking fixed coordinates")
    database.assert_coordinates_clean()
    storage.assert_all_coordinates_absent()
    wavs = build_all_controlled_wavs()

    report("Basic Pitch KEDA burst smoke: uploading three controlled WAVs")
    try:
        storage.upload_controlled_wavs(wavs)
    except Exception:
        # No durable event exists before `prepare_durable_burst` begins. A
        # timeout/error in a PUT can still have stored bytes, so remove only
        # the preflight-confirmed fixed keys before exposing the original error.
        with suppress(Exception):
            storage.delete_all_fixed_objects()
        raise

    report("Basic Pitch KEDA burst smoke: creating three durable requests")
    # Do not cleanup after this call starts. A lost response can mean that
    # PostgreSQL committed all three events and a real worker needs the WAVs.
    database.prepare_durable_burst(wavs)

    report("Basic Pitch KEDA burst smoke: waiting for deployed worker completions")
    observations = wait_for_worker_completions(
        database,
        sleep_function=sleep_function,
        monotonic=monotonic,
    )

    report("Basic Pitch KEDA burst smoke: verifying three private MIDI objects")
    wav_by_label = {wav.coordinate.label: wav for wav in wavs}
    verified: list[VerifiedMidi] = []
    for coordinate, observation in zip(BURST_COORDINATES, observations, strict=True):
        # `_completed_observations` already proves task_id is present. Keep an
        # explicit check for type narrowing and a safe future refactor guard.
        if observation.task_id is None:
            raise BasicPitchKedaBurstContractError("Completed burst task identity is missing.")
        verified.append(
            storage.verify_midi(
                coordinate=coordinate,
                task_id=observation.task_id,
                input_sha256=wav_by_label[coordinate.label].sha256,
            )
        )

    report("Basic Pitch KEDA burst smoke: cleaning exact successful evidence")
    # Delete physical private data before asking the success-only database
    # function to cascade its three Job/event/task records. If object deletion
    # fails, that durable evidence stays intact for an operator to investigate.
    storage.delete_all_fixed_objects()
    database.cleanup_successful_burst()
    report("Basic Pitch KEDA burst smoke passed")
    return tuple(verified)
