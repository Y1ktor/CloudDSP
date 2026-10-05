"""Translate reviewed durable failure codes for the owner-facing Job API.

Workers persist stable, non-sensitive codes. The browser should instead get
plain language, but this boundary must never echo an arbitrary worker error,
object path, command, or database diagnostic. Keep the map intentionally
finite; unknown codes retain the existing API behavior until reviewed.
"""

from __future__ import annotations


DEMUCS_TIMEOUT_CODES = frozenset({
    "demucs_process_timed_out",
    # Historical terminal jobs from the former three-attempt timeout policy.
    "demucs_process_timeout_retry_exhausted",
})

DEMUCS_TIMEOUT_MESSAGE = (
    "Stem separation exceeded the 12-minute processing limit. "
    "This job was stopped and will not retry. Please try a shorter audio file."
)


def owner_visible_job_error(*, status: object, error: object) -> object:
    """Replace only a terminal Demucs timeout code with approved user text."""

    if status == "failed" and isinstance(error, str) and error in DEMUCS_TIMEOUT_CODES:
        return DEMUCS_TIMEOUT_MESSAGE
    return error
