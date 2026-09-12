"""Unit tests for permanent Basic Pitch pre-model failure classification.

These tests create no database cursor, MinIO client, RabbitMQ delivery, model
process, worker loop, container, or Kubernetes resource.  They prove only the
finite mapping from already-raised exception types to durable safe codes.
"""

from __future__ import annotations

import unittest

from app.stem_download import (
    BasicPitchStemDownloadConsistencyError,
    BasicPitchStemDownloadUnavailable,
)
from app.stem_failure_classification import classify_basic_pitch_pre_model_terminal_failure
from app.stem_object import (
    BasicPitchPermanentStemVerificationError,
    BasicPitchStemStorageProtocolError,
    BasicPitchStemVerificationFailureCode,
)
from app.stem_task_terminal_failure import BasicPitchStemTerminalFailureCode


class BasicPitchStemFailureClassificationTests(unittest.TestCase):
    """Prove only immutable input-integrity failures receive terminal codes."""

    def test_head_object_permanent_codes_preserve_their_reviewed_values(self) -> None:
        """Every finite verifier category maps to the matching task-state code."""

        for verifier_code in BasicPitchStemVerificationFailureCode:
            with self.subTest(verifier_code=verifier_code):
                error = BasicPitchPermanentStemVerificationError(verifier_code)
                self.assertEqual(
                    classify_basic_pitch_pre_model_terminal_failure(error),
                    BasicPitchStemTerminalFailureCode(verifier_code.value),
                )

    def test_download_consistency_failure_becomes_the_separate_terminal_code(self) -> None:
        """A byte/header change after HeadObject must not be treated as an outage."""

        self.assertEqual(
            classify_basic_pitch_pre_model_terminal_failure(
                BasicPitchStemDownloadConsistencyError("private mismatch detail")
            ),
            BasicPitchStemTerminalFailureCode.DOWNLOAD_CHECKSUM_MISMATCH,
        )

    def test_outages_protocol_errors_and_unknown_errors_remain_for_later_policy(self) -> None:
        """Only a known permanent mismatch can invoke the terminal task adapter."""

        for error in (
            BasicPitchStemDownloadUnavailable("private outage detail"),
            BasicPitchStemStorageProtocolError("private protocol detail"),
            RuntimeError("private programming detail"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(classify_basic_pitch_pre_model_terminal_failure(error))


if __name__ == "__main__":
    unittest.main()
