"""Structural tests for the least-privilege Demucs recovery verifier Job.

These source-only checks lock the security boundary without connecting to the
local cluster. PostgreSQL integration remains the responsibility of the
explicit short-lived bootstrap Job and the end-to-end Demucs smoke test.
"""

from __future__ import annotations

import unittest
from pathlib import Path


SERVICE_DIRECTORY = Path(__file__).resolve().parents[1]


class RecoveryVerifierBootstrapManifestTests(unittest.TestCase):
    """Ensure recovery capability stays narrower than a payload-table grant."""

    def manifest(self) -> str:
        """Read the committed compatibility Job without parsing live state."""

        return (SERVICE_DIRECTORY / "demucs-recovery-verifier-bootstrap-job.yaml").read_text(
            encoding="utf-8"
        )

    def test_installs_a_boolean_security_definer_with_a_fixed_search_path(self) -> None:
        """The privileged SQL must validate payloads internally, not expose them."""

        manifest = self.manifest()

        for required_text in (
            "name: demucs-recovery-verifier-bootstrap",
            "CREATE OR REPLACE FUNCTION public.clouddsp_demucs_recovery_event_matches(",
            "RETURNS boolean",
            "SECURITY DEFINER",
            "SET search_path = pg_catalog, public",
            "event.payload = jsonb_build_object(",
            "task.lease_expires_at > CURRENT_TIMESTAMP",
            "JOIN public.jobs AS job",
            "job.expires_at > CURRENT_TIMESTAMP",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, manifest)

    def test_uses_a_named_dollar_quote_for_the_role_preflight(self) -> None:
        """Avoid Kubernetes collapsing an anonymous-dollar command escape."""

        manifest = self.manifest()

        self.assertIn("DO $required_demucs_role$", manifest)
        self.assertIn("$required_demucs_role$;", manifest)
        self.assertNotIn("DO $$", manifest)

    def test_grants_only_function_execution_and_keeps_payload_reads_denied(self) -> None:
        """The repair must not turn the runtime role into a payload reader."""

        manifest = self.manifest()

        self.assertIn("REVOKE ALL ON FUNCTION public.clouddsp_demucs_recovery_event_matches(", manifest)
        self.assertIn(') TO "clouddsp-demucs";', manifest)
        self.assertIn("AS can_verify_own_recovery_event", manifest)
        self.assertIn("AS can_read_outbox_payload", manifest)
        self.assertNotIn("GRANT SELECT (payload) ON TABLE public.outbox_events", manifest)
        self.assertNotIn("DEMUCS_DB_PASSWORD", manifest)


if __name__ == "__main__":
    unittest.main()
