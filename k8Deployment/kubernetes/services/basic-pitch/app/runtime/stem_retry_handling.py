"""Join reviewed transient stem classification to committed retry scheduling.

This narrow composition is the bridge between a caller that has *already
caught* an exception and the existing retry-scheduling transaction wrapper.
It does not catch around MinIO/model work itself, receive/acknowledge a
RabbitMQ delivery, sleep, reconnect, arrange redelivery, or decide what to do
when the bounded attempt budget is exhausted.

Separating this bridge makes all outcomes explicit: a caller can distinguish an
unclassified error (which it must re-raise or delegate), a committed durable
retry schedule, a committed final-attempt exhaustion result, and a no-row
result where the worker must stop. The final-attempt decision is made from the
immutable lease evidence before either guarded SQL statement runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.runtime.stem_retry_classification import classify_basic_pitch_pre_model_storage_retry
from app.db.stem_task_retry_schedule import (
    DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
    BasicPitchStemRetrySchedule,
)
from app.db.stem_task_retry_schedule_commit import (
    BasicPitchStemRetryScheduleDatabase,
    commit_basic_pitch_stem_retry_schedule,
)
from app.db.stem_task_retry_exhaustion import (
    BasicPitchStemRetryExhaustion,
    BasicPitchStemRetryExhaustionCode,
)
from app.db.stem_task_retry_exhaustion_commit import (
    BasicPitchStemRetryExhaustionDatabase,
    commit_final_attempt_basic_pitch_stem_retry_exhaustion,
)
from app.db.task_lease import MAX_BASIC_PITCH_TASK_ATTEMPTS, BasicPitchTaskLease


class BasicPitchPreModelRetryDatabase(
    BasicPitchStemRetryScheduleDatabase,
    BasicPitchStemRetryExhaustionDatabase,
    Protocol,
):
    """The shared short-transaction capability for either pre-model outcome."""


class BasicPitchPreModelRetryHandlingDisposition(StrEnum):
    """The only outcomes of the classifier-to-commit bridge."""

    # This error belongs to a different policy. The bridge performed no SQL.
    UNCLASSIFIED = "unclassified"
    # PostgreSQL committed this task's durable `retry_scheduled` state after
    # a first or second transient storage failure.
    RETRY_SCHEDULED = "retry_scheduled"
    # PostgreSQL committed the third transient storage failure as terminal.
    RETRY_EXHAUSTED = "retry_exhausted"
    # The selected guarded statement found no current eligible lease. This
    # covers expiry/recovery/state races without manufacturing an outcome.
    NO_DURABLE_RESULT = "no_durable_result"


@dataclass(frozen=True)
class BasicPitchPreModelRetryHandling:
    """Non-sensitive outcome of one reviewed pre-model retry handoff."""

    disposition: BasicPitchPreModelRetryHandlingDisposition
    retry_schedule: BasicPitchStemRetrySchedule | None = None
    retry_exhaustion: BasicPitchStemRetryExhaustion | None = None

    def __post_init__(self) -> None:
        """Require exactly one committed evidence type for each durable outcome."""

        if self.disposition is BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED:
            if (
                not isinstance(self.retry_schedule, BasicPitchStemRetrySchedule)
                or self.retry_exhaustion is not None
            ):
                raise TypeError("A scheduled Basic Pitch retry requires committed retry evidence.")
            return
        if self.disposition is BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED:
            if self.retry_schedule is not None or not isinstance(
                self.retry_exhaustion, BasicPitchStemRetryExhaustion
            ):
                raise TypeError("An exhausted Basic Pitch retry requires committed terminal evidence.")
            return
        if self.disposition in {
            BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED,
            BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT,
        }:
            if self.retry_schedule is not None or self.retry_exhaustion is not None:
                raise TypeError("A non-durable Basic Pitch retry outcome cannot include evidence.")
            return
        raise TypeError("Basic Pitch pre-model retry handling disposition is invalid.")


def handle_basic_pitch_pre_model_storage_retry(
    error: BaseException,
    *,
    database: BasicPitchPreModelRetryDatabase,
    lease: BasicPitchTaskLease,
    retry_after_seconds: int = DEFAULT_BASIC_PITCH_RETRY_AFTER_SECONDS,
) -> BasicPitchPreModelRetryHandling:
    """Commit retry or exhaustion only for a reviewed transient storage failure.

    The function receives an exception only after its caller has caught it;
    this helper deliberately owns no broad ``try``/``except`` around external
    work. An unclassified error returns a distinct result and causes no
    database call, allowing the caller to preserve the original exception.

    A classified first/second attempt uses the bounded durable retry schedule.
    A classified third attempt cannot schedule a fourth run, so it instead
    commits the finite terminal exhaustion code. Both branches use their own
    guarded short transaction wrapper; a no-row race returns no durable
    evidence and must not be replaced by a worker-made result.
    """

    failure_code = classify_basic_pitch_pre_model_storage_retry(error)
    if failure_code is None:
        return BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.UNCLASSIFIED,
        )
    if not isinstance(lease, BasicPitchTaskLease):
        raise TypeError("lease must be BasicPitchTaskLease.")
    if lease.attempt_count == MAX_BASIC_PITCH_TASK_ATTEMPTS:
        retry_exhaustion = commit_final_attempt_basic_pitch_stem_retry_exhaustion(
            database=database,
            lease=lease,
            failure_code=BasicPitchStemRetryExhaustionCode.STORAGE_UNAVAILABLE,
        )
        if retry_exhaustion is None:
            return BasicPitchPreModelRetryHandling(
                disposition=BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT,
            )
        return BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_EXHAUSTED,
            retry_exhaustion=retry_exhaustion,
        )

    retry_schedule = commit_basic_pitch_stem_retry_schedule(
        database=database,
        lease=lease,
        retry_after_seconds=retry_after_seconds,
        failure_code=failure_code,
    )
    if retry_schedule is None:
        return BasicPitchPreModelRetryHandling(
            disposition=BasicPitchPreModelRetryHandlingDisposition.NO_DURABLE_RESULT,
        )
    return BasicPitchPreModelRetryHandling(
        disposition=BasicPitchPreModelRetryHandlingDisposition.RETRY_SCHEDULED,
        retry_schedule=retry_schedule,
    )
