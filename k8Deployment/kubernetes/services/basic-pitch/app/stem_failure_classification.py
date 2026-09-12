"""Classify only permanent Basic Pitch input-verification errors.

This is a deliberately small translation boundary between the existing MinIO
verification/download modules and durable task-result codes.  It does not
catch errors, open PostgreSQL, call RabbitMQ, schedule retries, or invoke a
model.  A later execution-handling layer can use its return value to choose:

* a terminal PostgreSQL task update for the finite permanent cases below; or
* a distinct retry/fatal path when this function returns ``None``.

Keeping this decision explicit is important because a temporary MinIO outage
and a checksum mismatch may both arise near object storage, but they need
opposite durable outcomes.
"""

from __future__ import annotations

from app.stem_download import BasicPitchStemDownloadConsistencyError
from app.stem_object import (
    BasicPitchPermanentStemVerificationError,
    BasicPitchStemVerificationFailureCode,
)
from app.stem_task_terminal_failure import BasicPitchStemTerminalFailureCode


class BasicPitchStemFailureClassificationError(RuntimeError):
    """A supposedly permanent verifier error lacks a reviewed finite code."""


def _head_object_failure_code(
    error: BasicPitchPermanentStemVerificationError,
) -> BasicPitchStemTerminalFailureCode:
    """Translate one trusted HeadObject failure without retaining its response."""

    verifier_code = getattr(error, "failure_code", None)
    if not isinstance(verifier_code, BasicPitchStemVerificationFailureCode):
        raise BasicPitchStemFailureClassificationError(
            "Basic Pitch permanent stem verification failure is invalid."
        )
    try:
        # The durable code uses the same reviewed strings as the verifier, but
        # conversion through the enum prevents a future new verifier value from
        # silently becoming an unreviewed PostgreSQL error code.
        return BasicPitchStemTerminalFailureCode(verifier_code.value)
    except ValueError as classification_error:
        raise BasicPitchStemFailureClassificationError(
            "Basic Pitch permanent stem verification failure is invalid."
        ) from classification_error


def classify_basic_pitch_pre_model_terminal_failure(
    error: BaseException,
) -> BasicPitchStemTerminalFailureCode | None:
    """Return a terminal code only for immutable input-integrity failures.

    A permanent ``HeadObject`` mismatch means the current object cannot safely
    represent the exact claimed stem.  A download consistency error means the
    object changed or disagreed between metadata and byte streaming.  Neither
    becomes a broker retry/DLQ action: the already-acknowledged task must be
    marked terminal by a caller using the returned code.

    All other exceptions—including storage unavailability, malformed storage
    protocol responses, database errors, model failures, and programming
    errors—return ``None``.  A later runtime classifies them under separate
    retry/fatal policy rather than accidentally calling this terminal adapter.
    """

    if isinstance(error, BasicPitchPermanentStemVerificationError):
        return _head_object_failure_code(error)
    if isinstance(error, BasicPitchStemDownloadConsistencyError):
        return BasicPitchStemTerminalFailureCode.DOWNLOAD_CHECKSUM_MISMATCH
    return None
