"""Unit tests for bounded post-``running`` Demucs failure classification.

The classifier makes no database, MinIO, RabbitMQ, model, Pod, or Kubernetes
call. It proves only which already-raised safe exception types receive the
later three-attempt retry policy.
"""

from __future__ import annotations

import unittest

from app.demucs_artifact_hash import (
    DemucsArtifactHashConsistencyError,
    DemucsArtifactHashContractError,
    DemucsArtifactHashPathError,
)
from app.demucs_artifact_upload import (
    DemucsArtifactUploadConsistencyError,
    DemucsArtifactUploadContractError,
    DemucsArtifactUploadPathError,
    DemucsArtifactUploadUnavailable,
)
from app.demucs_artifacts import (
    DemucsArtifactContractError,
    DemucsArtifactInventoryMismatch,
    DemucsArtifactPathError,
)
from app.demucs_process import (
    DemucsProcessContractError,
    DemucsProcessError,
    DemucsProcessFailed,
    DemucsProcessTimedOut,
    DemucsProcessUnavailable,
)
from app.postgresql import DemucsDatabaseUnavailable
from app.running_failure_classification import (
    DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE,
    DemucsRunningFailureDisposition,
    DemucsRunningRetryCode,
    DemucsRunningRetryExhaustionCode,
    DemucsRunningTerminalCode,
    classify_demucs_running_failure,
    retry_exhaustion_code_for_running_failure,
)


class DemucsRunningFailureClassificationTests(unittest.TestCase):
    """Prove the after-model policy is finite and fails closed by default."""

    def test_process_failures_receive_distinct_bounded_retry_categories(self) -> None:
        """Start and nonzero exit failures retain useful safe retry facts."""

        expected_codes = {
            DemucsProcessError("private start detail"): DemucsRunningRetryCode.PROCESS_START_FAILED,
            DemucsProcessFailed("private exit detail"): DemucsRunningRetryCode.PROCESS_FAILED,
        }
        for error, expected_code in expected_codes.items():
            with self.subTest(error=type(error).__name__):
                classification = classify_demucs_running_failure(error)
                self.assertEqual(
                    classification.disposition,
                    DemucsRunningFailureDisposition.RETRY_SCHEDULED,
                )
                self.assertEqual(classification.retry_code, expected_code)

    def test_twelve_minute_process_timeout_is_terminal_without_retry(self) -> None:
        """A CPU deadline is never converted into another Demucs attempt."""

        classification = classify_demucs_running_failure(DemucsProcessTimedOut("private process detail"))
        self.assertEqual(classification.disposition, DemucsRunningFailureDisposition.TERMINAL_FAILURE)
        self.assertEqual(classification.terminal_code, DemucsRunningTerminalCode.PROCESS_TIMED_OUT)
        self.assertIsNone(classification.retry_code)

    def test_output_hash_and_upload_failures_use_their_reviewed_categories(self) -> None:
        """Private partial artifacts may be retried only under stable object keys."""

        expected_codes = {
            DemucsArtifactInventoryMismatch("private output detail"): DemucsRunningRetryCode.OUTPUT_INVALID,
            DemucsArtifactPathError("private path detail"): DemucsRunningRetryCode.OUTPUT_INVALID,
            DemucsArtifactHashPathError("private hash-path detail"): DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
            DemucsArtifactHashConsistencyError("private hash detail"): DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
            DemucsArtifactUploadPathError("private upload-path detail"): DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
            DemucsArtifactUploadConsistencyError("private upload detail"): DemucsRunningRetryCode.ARTIFACT_INTEGRITY_UNAVAILABLE,
            DemucsArtifactUploadUnavailable("private MinIO detail"): DemucsRunningRetryCode.ARTIFACT_STORAGE_UNAVAILABLE,
        }
        for error, expected_code in expected_codes.items():
            with self.subTest(error=type(error).__name__):
                classification = classify_demucs_running_failure(error)
                self.assertEqual(
                    classification.disposition,
                    DemucsRunningFailureDisposition.RETRY_SCHEDULED,
                )
                self.assertEqual(classification.retry_code, expected_code)

    def test_contract_image_database_and_unknown_errors_remain_unclassified(self) -> None:
        """Operator/actionable faults cannot silently become user-job retries."""

        for error in (
            DemucsProcessUnavailable("private image detail"),
            DemucsProcessContractError("private command detail"),
            DemucsArtifactContractError("private output contract detail"),
            DemucsArtifactHashContractError("private hash contract detail"),
            DemucsArtifactUploadContractError("private upload contract detail"),
            DemucsDatabaseUnavailable("private database detail"),
            RuntimeError("private programming detail"),
        ):
            with self.subTest(error=type(error).__name__):
                classification = classify_demucs_running_failure(error)
                self.assertEqual(
                    classification.disposition,
                    DemucsRunningFailureDisposition.UNCLASSIFIED,
                )
                self.assertIsNone(classification.retry_code)

    def test_every_retry_category_has_one_explicit_terminal_exhaustion_category(self) -> None:
        """Attempt three never relies on dynamic string construction or a fourth run."""

        self.assertEqual(set(DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE), set(DemucsRunningRetryCode))
        self.assertEqual(
            set(DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE.values()),
            set(DemucsRunningRetryExhaustionCode),
        )
        for retry_code, terminal_code in DEMUCS_RUNNING_RETRY_EXHAUSTION_BY_RETRY_CODE.items():
            with self.subTest(retry_code=retry_code):
                self.assertEqual(retry_exhaustion_code_for_running_failure(retry_code), terminal_code)


if __name__ == "__main__":
    unittest.main()
