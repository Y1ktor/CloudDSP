"""Unit tests for temporary Basic Pitch pre-model storage classification.

These tests create no database cursor, MinIO client, RabbitMQ delivery, model
process, worker loop, container, or Kubernetes resource. They prove only the
finite mapping from already-raised safe exception types to one retry code.
"""

from __future__ import annotations

import unittest

from app.artifacts.midi_artifact_upload import BasicPitchMidiUploadUnavailable
from app.artifacts.stem_download import (
    BasicPitchStemDownloadConsistencyError,
    BasicPitchStemDownloadUnavailable,
)
from app.artifacts.stem_object import (
    BasicPitchPermanentStemVerificationError,
    BasicPitchStemStorageProtocolError,
    BasicPitchStemStorageUnavailable,
    BasicPitchStemVerificationFailureCode,
)
from app.runtime.stem_retry_classification import classify_basic_pitch_pre_model_storage_retry
from app.db.stem_task_retry_schedule import BasicPitchStemRetryScheduleCode


class BasicPitchStemRetryClassificationTests(unittest.TestCase):
    """Prove only temporary pre-model private-stem MinIO faults can schedule retry."""

    def test_head_or_download_storage_outage_uses_one_finite_retry_code(self) -> None:
        """Both safe storage wrappers mean the immutable input may be tried later."""

        for error in (
            BasicPitchStemStorageUnavailable("private HeadObject outage detail"),
            BasicPitchStemDownloadUnavailable("private GetObject outage detail"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(
                    classify_basic_pitch_pre_model_storage_retry(error),
                    BasicPitchStemRetryScheduleCode.STORAGE_UNAVAILABLE,
                )

    def test_permanent_protocol_post_model_and_unknown_errors_do_not_become_pre_model_retries(self) -> None:
        """Only existing safe wrappers can release the still-unstarted task lease."""

        for error in (
            BasicPitchPermanentStemVerificationError(BasicPitchStemVerificationFailureCode.OBJECT_MISSING),
            BasicPitchStemDownloadConsistencyError("private checksum mismatch detail"),
            BasicPitchStemStorageProtocolError("private protocol detail"),
            BasicPitchMidiUploadUnavailable("private post-model outage detail"),
            RuntimeError("private programming detail"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(classify_basic_pitch_pre_model_storage_retry(error))


if __name__ == "__main__":
    unittest.main()
