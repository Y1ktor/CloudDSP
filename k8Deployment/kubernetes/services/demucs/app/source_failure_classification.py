"""Classify finite pre-model Demucs source failures without mutating work.

The one-task runtime has a deliberately important split before it starts the
Demucs model:

``acknowledged lease -> HeadObject -> GetObject -> FFprobe -> running``

Only the three source boundaries on the left can raise the exception classes
recognized here. A caller may later use this result only with a
lease-token-guarded transition from ``leased`` to either ``retry_scheduled``
or ``failed``. It must *not* reuse this classifier after the task became
``running``: model, output, upload, and database failures have different
durability and retry semantics.

This is a pure translation boundary. It neither catches an exception around
I/O nor opens PostgreSQL, changes a task or Job, sends an AMQP action, sleeps,
starts a worker loop, or invokes FFprobe/Demucs. Keeping the finite mapping
here prevents raw MinIO errors, object keys, FFprobe output, child stderr, or
tracebacks from becoming durable ``last_error_code`` values in a later SQL
adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.audio_probe import DemucsAudioProbeFailureCode, DemucsPermanentAudioProbeError
from app.source_download import DemucsSourceDownloadConsistencyError, DemucsSourceDownloadUnavailable
from app.source_object import (
    DemucsPermanentSourceVerificationError,
    DemucsSourceStorageUnavailable,
    DemucsSourceVerificationFailureCode,
)


class DemucsPreModelFailureDisposition(StrEnum):
    """The only durable decisions this pre-``running`` classifier may suggest.

    ``UNCLASSIFIED`` means the caller must preserve the original exception for
    a later, separately reviewed policy. It is not a permissive fallback that
    retries arbitrary programming, process, database, or storage-protocol
    faults.
    """

    TERMINAL_FAILURE = "terminal_failure"
    RETRY_SCHEDULED = "retry_scheduled"
    UNCLASSIFIED = "unclassified"


class DemucsPreModelTerminalFailureCode(StrEnum):
    """Finite non-sensitive terminal codes for immutable source rejections.

    The first seven strings preserve their audited verifier categories. The
    final value covers the time-of-check/time-of-use defense: an object that
    passed HeadObject later disagreed while GetObject streamed it. None are
    user-facing text or raw dependency diagnostics.
    """

    OBJECT_MISSING = "source_object_missing"
    SIZE_LIMIT_EXCEEDED = "source_size_limit_exceeded"
    UNSUPPORTED_CONTENT_TYPE = "source_content_type_unsupported"
    METADATA_MISMATCH = "source_metadata_mismatch"
    AUDIO_STREAM_MISSING = "source_audio_stream_missing"
    INVALID_DURATION = "source_duration_invalid"
    DURATION_LIMIT_EXCEEDED = "source_duration_limit_exceeded"
    DOWNLOAD_CONSISTENCY_MISMATCH = "source_download_consistency_mismatch"


class DemucsPreModelRetryCode(StrEnum):
    """The reviewed transient source-storage category for attempts one/two.

    HeadObject and GetObject use different safe wrappers but express the same
    durable fact: MinIO could not currently complete the request. The immutable
    source has not been disproved, so a later three-attempt decision may
    schedule it with PostgreSQL time.
    """

    STORAGE_UNAVAILABLE = "demucs_source_storage_unavailable"


@dataclass(frozen=True)
class DemucsPreModelFailureClassification:
    """One explicit source-failure classification with no private evidence.

    Exactly one code accompanies each durable disposition. This defensive
    shape prevents a future transaction layer from receiving both a terminal
    and retry code, or from writing a code for an unclassified error.
    """

    disposition: DemucsPreModelFailureDisposition
    terminal_failure_code: DemucsPreModelTerminalFailureCode | None = None
    retry_code: DemucsPreModelRetryCode | None = None

    def __post_init__(self) -> None:
        """Keep the output a total, unambiguous decision for its next layer."""

        if self.disposition is DemucsPreModelFailureDisposition.TERMINAL_FAILURE:
            if not isinstance(self.terminal_failure_code, DemucsPreModelTerminalFailureCode) or self.retry_code is not None:
                raise TypeError("A terminal Demucs source failure requires only a terminal code.")
            return
        if self.disposition is DemucsPreModelFailureDisposition.RETRY_SCHEDULED:
            if self.terminal_failure_code is not None or not isinstance(self.retry_code, DemucsPreModelRetryCode):
                raise TypeError("A retryable Demucs source failure requires only a retry code.")
            return
        if self.disposition is DemucsPreModelFailureDisposition.UNCLASSIFIED:
            if self.terminal_failure_code is not None or self.retry_code is not None:
                raise TypeError("An unclassified Demucs source failure cannot include a code.")
            return
        raise TypeError("Demucs source-failure disposition is invalid.")


def _terminal_code_from_verifier_error(
    error: DemucsPermanentSourceVerificationError | DemucsPermanentAudioProbeError,
) -> DemucsPreModelTerminalFailureCode:
    """Translate only a known verifier enum into a reviewed durable code."""

    verifier_code = getattr(error, "failure_code", None)
    if not isinstance(verifier_code, (DemucsSourceVerificationFailureCode, DemucsAudioProbeFailureCode)):
        raise RuntimeError("Demucs permanent source failure has no reviewed code.")
    try:
        # Enum conversion makes a future verifier category fail closed until it
        # is deliberately added to this task-state vocabulary.
        return DemucsPreModelTerminalFailureCode(verifier_code.value)
    except ValueError as classification_error:
        raise RuntimeError("Demucs permanent source failure has no reviewed code.") from classification_error


def classify_demucs_pre_model_failure(error: BaseException) -> DemucsPreModelFailureClassification:
    """Classify only proven source outcomes before a task is ``running``.

    Metadata/media rejection or a HeadObject/GetObject consistency mismatch
    cannot improve through automatic retry of the same immutable task, so it
    receives a terminal code. The explicit storage-availability wrappers have
    opposite semantics and receive one retry category. Storage protocol,
    FFprobe process/protocol, model/output, database, and unknown failures stay
    unclassified; their policy must be intentionally added rather than being
    smuggled into this source-retry path.
    """

    if isinstance(error, (DemucsPermanentSourceVerificationError, DemucsPermanentAudioProbeError)):
        return DemucsPreModelFailureClassification(
            disposition=DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
            terminal_failure_code=_terminal_code_from_verifier_error(error),
        )
    if isinstance(error, DemucsSourceDownloadConsistencyError):
        return DemucsPreModelFailureClassification(
            disposition=DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
            terminal_failure_code=DemucsPreModelTerminalFailureCode.DOWNLOAD_CONSISTENCY_MISMATCH,
        )
    if isinstance(error, (DemucsSourceStorageUnavailable, DemucsSourceDownloadUnavailable)):
        return DemucsPreModelFailureClassification(
            disposition=DemucsPreModelFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
        )
    return DemucsPreModelFailureClassification(
        disposition=DemucsPreModelFailureDisposition.UNCLASSIFIED,
    )
