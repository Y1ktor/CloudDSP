"""Classify reviewed retryable Demucs failures after ``leased -> running``.

This complements the pre-model source classifier. Once PostgreSQL has
committed ``running``, the worker may have spent CPU time or written some
private stem objects. It must therefore use a different state transition from
the earlier ``leased`` policy. The finite categories here identify failures for
which a fresh bounded attempt is safe because:

* source input has already passed its immutable admission checks; and
* output object keys are deterministic and retry-overwritable, so a partial
  private upload cannot become a second public artifact set.

There is intentionally no immediately-terminal category in this first running
policy. A reviewed model/output/storage disruption receives a durable retry on
attempts one/two; the exact same finite category becomes a terminal
retry-exhaustion result on attempt three. Image/command-contract faults,
database failures, completion-contract faults, and unknown exceptions remain
unclassified: retrying or failing a user Job from those facts would hide an
operator/actionable defect behind an unreviewed task result.

This module is pure. It does not catch a runtime attempt, update PostgreSQL,
touch MinIO/RabbitMQ, sleep, renew a lease, invoke Demucs, or change a Pod/KEDA
resource. The later commit and runtime adapters consume only its finite output
with a running lease-token-guarded durable transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.demucs_artifact_hash import (
    DemucsArtifactHashConsistencyError,
    DemucsArtifactHashPathError,
)
from app.demucs_artifact_upload import (
    DemucsArtifactUploadConsistencyError,
    DemucsArtifactUploadPathError,
    DemucsArtifactUploadUnavailable,
)
from app.demucs_artifacts import DemucsArtifactInventoryMismatch, DemucsArtifactPathError
from app.demucs_process import DemucsProcessError, DemucsProcessFailed, DemucsProcessTimedOut


class DemucsRunningFailureDisposition(StrEnum):
    """The finite outcomes of this after-model classifier."""

    RETRY_SCHEDULED = "retry_scheduled"
    UNCLASSIFIED = "unclassified"


class DemucsRunningRetryCode(StrEnum):
    """Safe non-sensitive running-phase categories for `last_error_code`.

    These values contain no command, model stderr, scratch path, object key,
    MinIO endpoint, credential, exception message, or stack trace. They record
    the operational class the future retry state machine may safely retry.
    """

    PROCESS_START_FAILED = "demucs_process_start_failed"
    PROCESS_TIMED_OUT = "demucs_process_timed_out"
    PROCESS_FAILED = "demucs_process_failed"
    OUTPUT_INVALID = "demucs_output_invalid"
    ARTIFACT_INTEGRITY_UNAVAILABLE = "demucs_artifact_integrity_unavailable"
    ARTIFACT_STORAGE_UNAVAILABLE = "demucs_artifact_storage_unavailable"


class DemucsRunningRetryExhaustionCode(StrEnum):
    """Terminal evidence selected when a running retry reaches attempt three."""

    PROCESS_START_FAILED = "demucs_process_start_retry_exhausted"
    PROCESS_TIMED_OUT = "demucs_process_timeout_retry_exhausted"
    PROCESS_FAILED = "demucs_process_failure_retry_exhausted"
    OUTPUT_INVALID = "demucs_output_retry_exhausted"
    ARTIFACT_INTEGRITY_UNAVAILABLE = "demucs_artifact_integrity_retry_exhausted"
    ARTIFACT_STORAGE_UNAVAILABLE = "demucs_artifact_storage_retry_exhausted"


# Keep this finite mapping explicit rather than deriving strings at runtime. A
# future retry code cannot silently invent a terminal vocabulary without both
# the application review here and a corresponding database-transition review.
DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE = {
    DemucsRunningRetryCode.PROCESS_START_FAILED: DemucsRunningRetryExhaustionCode.PROCESS_START_FAILED,
    DemucsRunningRetryCode.PROCESS_TIMED_OUT: DemucsRunningRetryExhaustionCode.PROCESS_TIMED_OUT,
    DemucsRunningRetryCode.PROCESS_FAILED: DemucsRunningRetryExhaustionCode.PROCESS_FAILED,
    DemucsRunningRetryCode.OUTPUT_INVALID: DemucsRunningRetryExhaustionCode.OUTPUT_INVALID,
    DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE: DemucsRunningRetryExhaustionCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
    DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE: DemucsRunningRetryExhaustionCode.ARTIFACT_STORAGE_UNAVAILABLE,
}


@dataclass(frozen=True)
class DemucsRunningFailureClassification:
    """One classified running failure without carrying exception details."""

    disposition: DemucsRunningFailureDisposition
    retry_code: DemucsRunningRetryCode | None = None

    def __post_init__(self) -> None:
        """Ensure only a reviewed retry outcome carries a durable code."""

        if self.disposition is DemucsRunningFailureDisposition.RETRY_SCHEDULED:
            if not isinstance(self.retry_code, DemucsRunningRetryCode):
                raise TypeError("A retryable running Demucs failure requires a retry code.")
            return
        if self.disposition is DemucsRunningFailureDisposition.UNCLASSIFIED:
            if self.retry_code is not None:
                raise TypeError("An unclassified running Demucs failure cannot include a retry code.")
            return
        raise TypeError("Demucs running-failure disposition is invalid.")


def retry_exhaustion_code_for_running_failure(
    retry_code: DemucsRunningRetryCode,
) -> DemucsRunningRetryExhaustionCode:
    """Return the one terminal category paired with a reviewed retry code."""

    if not isinstance(retry_code, DemucsRunningRetryCode):
        raise TypeError("retry_code must be DemucsRunningRetryCode.")
    try:
        return DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE[retry_code]
    except KeyError as error:  # Defensive: enums/mappings may evolve separately.
        raise RuntimeError("Demucs running retry exhaustion mapping is invalid.") from error


def classify_demucs_running_failure(error: BaseException) -> DemucsRunningFailureClassification:
    """Map only bounded retryable failures that occur after `running` starts.

    A process timeout/nonzero/start failure, unsafe/incomplete transient output
    tree, local post-model artifact change, or MinIO output interruption can
    safely retry because the later result transition will still match the exact
    running lease and stable output keys. `DemucsProcessUnavailable` and every
    contract error are deliberately excluded despite sharing base classes: they
    identify an image/configuration/programming defect, not user work that a
    generic retry should hide.
    """

    # Check the concrete process types before the base class. Exact base-class
    # start failure is retryable, but unrecognized future subclasses fail
    # closed instead of acquiring a retry policy by inheritance alone.
    if type(error) is DemucsProcessError:
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.PROCESS_START_FAILED,
        )
    if isinstance(error, DemucsProcessTimedOut):
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.PROCESS_TIMED_OUT,
        )
    if isinstance(error, DemucsProcessFailed):
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.PROCESS_FAILED,
        )
    if isinstance(error, (DemucsArtifactInventoryMismatch, DemucsArtifactPathError)):
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.OUTPUT_INVALID,
        )
    if isinstance(
        error,
        (
            DemucsArtifactHashPathError,
            DemucsArtifactHashConsistencyError,
            DemucsArtifactUploadPathError,
            DemucsArtifactUploadConsistencyError,
        ),
    ):
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
        )
    if isinstance(error, DemucsArtifactUploadUnavailable):
        return DemucsRunningFailureClassification(
            disposition=DemucsRunningFailureDisposition.RETRY_SCHEDULED,
            retry_code=DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE,
        )
    return DemucsRunningFailureClassification(
        disposition=DemucsRunningFailureDisposition.UNCLASSIFIED,
    )
