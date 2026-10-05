"""Classify only reviewed transient Basic Pitch *input-stem* storage failures.

This is the complementary decision to ``stem_failure_classification.py``.
It recognizes the two safe exceptions raised while Basic Pitch is still in the
pre-model input path: the initial private-stem ``HeadObject`` request cannot
reach MinIO, or the subsequent private-stem ``GetObject``/stream cannot
complete. Both failures may succeed when retried later without changing the
durable stem identity.

The module does not catch an exception, open PostgreSQL, call MinIO/RabbitMQ,
choose an attempt-exhaustion result, invoke the model, or run a worker loop.
In particular, it deliberately excludes MinIO errors during MIDI upload or
MIDI verification because those occur after model work starts and need a
separate post-model retry policy.
"""

from __future__ import annotations

from app.artifacts.stem_download import BasicPitchStemDownloadUnavailable
from app.artifacts.stem_object import BasicPitchStemStorageUnavailable
from app.db.stem_task_retry_schedule import BasicPitchStemRetryScheduleCode


def classify_basic_pitch_pre_model_storage_retry(
    error: BaseException,
) -> BasicPitchStemRetryScheduleCode | None:
    """Return the sole retry code only for temporary private-stem MinIO failure.

    ``BasicPitchStemStorageUnavailable`` and
    ``BasicPitchStemDownloadUnavailable`` are already safe wrappers: their
    originating S3/SDK diagnostics were kept only as exception causes rather
    than becoming normal messages or durable state. Mapping them to one finite
    code prevents an endpoint, key, credential, raw response, or stack trace
    from entering ``processing_tasks.last_error_code``.

    Every other error returns ``None``. Permanent input mismatches belong to
    the terminal classifier; protocol/database/model/output failures and
    unexpected programming errors must remain visible to their own later
    policy rather than accidentally releasing this pre-model lease.
    """

    if isinstance(error, (BasicPitchStemStorageUnavailable, BasicPitchStemDownloadUnavailable)):
        return BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE
    return None
