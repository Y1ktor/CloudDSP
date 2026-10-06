"""Unit tests for finite pre-model Demucs source-failure classification.

The classifier intentionally performs no PostgreSQL, MinIO, RabbitMQ, model,
container, or Kubernetes operation. These tests prove only the safe mapping
that a later token-guarded durable transition may consume.
"""

from __future__ import annotations

import unittest

from app.processing.audio_probe import (
    DemucsAudioProbeFailureCode,
    DemucsAudioProbeProtocolError,
    DemucsPermanentAudioProbeError,
)
from app.processing.demucs_process import DemucsProcessTimedOut
from app.artifacts.source_download import (
    DemucsSourceDownloadConsistencyError,
    DemucsSourceDownloadUnavailable,
)
from app.runtime.source_failure_classification import (
    DemucsPreModelFailureDisposition,
    DemucsPreModelRetryCode,
    DemucsPreModelTerminalFailureCode,
    classify_demucs_pre_model_failure,
)
from app.artifacts.source_object import (
    DemucsPermanentSourceVerificationError,
    DemucsSourceStorageProtocolError,
    DemucsSourceStorageUnavailable,
    DemucsSourceVerificationFailureCode,
)


class DemucsSourceFailureClassificationTests(unittest.TestCase):
    """Prove only immutable input failures or known outages get a decision."""

    def test_source_verifier_codes_preserve_reviewed_durable_values(self) -> None:
        """Each finite HeadObject category becomes the matching terminal code."""

        for verifier_code in DemucsSourceVerificationFailureCode:
            with self.subTest(verifier_code=verifier_code):
                classification = classify_demucs_pre_model_failure(
                    DemucsPermanentSourceVerificationError(verifier_code)
                )
                self.assertEqual(
                    classification.disposition,
                    DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
                )
                self.assertEqual(
                    classification.terminal_failure_code,
                    DemucsPreModelTerminalFailureCode(verifier_code.value),
                )
                self.assertIsNone(classification.retry_code)

    def test_audio_probe_categories_preserve_reviewed_durable_values(self) -> None:
        """Media-limit rejections remain terminal without retaining probe output."""

        for verifier_code in DemucsAudioProbeFailureCode:
            with self.subTest(verifier_code=verifier_code):
                classification = classify_demucs_pre_model_failure(
                    DemucsPermanentAudioProbeError(verifier_code)
                )
                self.assertEqual(
                    classification.disposition,
                    DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
                )
                self.assertEqual(
                    classification.terminal_failure_code,
                    DemucsPreModelTerminalFailureCode(verifier_code.value),
                )
                self.assertIsNone(classification.retry_code)

    def test_download_consistency_mismatch_is_a_separate_terminal_category(self) -> None:
        """Changed post-HeadObject bytes cannot be retried as a mere outage."""

        classification = classify_demucs_pre_model_failure(
            DemucsSourceDownloadConsistencyError("private mismatch detail")
        )

        self.assertEqual(
            classification.disposition,
            DemucsPreModelFailureDisposition.TERMINAL_FAILURE,
        )
        self.assertEqual(
            classification.terminal_failure_code,
            DemucsPreModelTerminalFailureCode.DOWNLOAD_CONSISTENCY_MISMATCH,
        )
        self.assertIsNone(classification.retry_code)

    def test_head_or_download_unavailability_maps_to_one_retry_category(self) -> None:
        """The same MinIO availability fact has one durable retry vocabulary."""

        for error in (
            DemucsSourceStorageUnavailable("private HeadObject outage detail"),
            DemucsSourceDownloadUnavailable("private GetObject outage detail"),
        ):
            with self.subTest(error=type(error).__name__):
                classification = classify_demucs_pre_model_failure(error)
                self.assertEqual(
                    classification.disposition,
                    DemucsPreModelFailureDisposition.RETRY_SCHEDULED,
                )
                self.assertIsNone(classification.terminal_failure_code)
                self.assertEqual(
                    classification.retry_code,
                    DemucsPreModelRetryCode.STORAGE_UNAVAILABLE,
                )

    def test_protocol_process_and_unknown_errors_remain_unclassified(self) -> None:
        """Only reviewed pre-model categories may reach the future SQL adapter."""

        for error in (
            DemucsSourceStorageProtocolError("private protocol detail"),
            DemucsAudioProbeProtocolError("private FFprobe output detail"),
            DemucsProcessTimedOut("private process detail"),
            RuntimeError("private programming detail"),
        ):
            with self.subTest(error=type(error).__name__):
                classification = classify_demucs_pre_model_failure(error)
                self.assertEqual(
                    classification.disposition,
                    DemucsPreModelFailureDisposition.UNCLASSIFIED,
                )
                self.assertIsNone(classification.terminal_failure_code)
                self.assertIsNone(classification.retry_code)


if __name__ == "__main__":
    unittest.main()
