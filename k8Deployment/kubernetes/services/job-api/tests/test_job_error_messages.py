"""Dependency-free tests for the owner-visible Demucs timeout copy."""

from __future__ import annotations

import unittest

from app.job_error_messages import DEMUCS_TIMEOUT_MESSAGE, owner_visible_job_error


class JobErrorMessagesTests(unittest.TestCase):
    """Keep runtime codes stable while giving users an actionable result."""

    def test_current_and_legacy_timeout_codes_are_terminal_plain_language(self) -> None:
        for code in ("demucs_process_timed_out", "demucs_process_timeout_retry_exhausted"):
            with self.subTest(code=code):
                self.assertEqual(owner_visible_job_error(status="failed", error=code), DEMUCS_TIMEOUT_MESSAGE)
        self.assertIn("will not retry", DEMUCS_TIMEOUT_MESSAGE)

    def test_nonterminal_or_unreviewed_error_is_not_rewritten(self) -> None:
        self.assertEqual(
            owner_visible_job_error(status="source_uploaded", error="demucs_process_timed_out"),
            "demucs_process_timed_out",
        )
        self.assertEqual(owner_visible_job_error(status="failed", error="other_safe_code"), "other_safe_code")
        self.assertIsNone(owner_visible_job_error(status="failed", error=None))


if __name__ == "__main__":
    unittest.main()
