"""Static checks for ADTOF's narrowly granted completion SQL capability.

This reads only the versioned bootstrap manifest as text. It does not apply the
Job, connect to PostgreSQL, expose a credential, or create Kubernetes state.
"""

from __future__ import annotations

from pathlib import Path
import unittest


BOOTSTRAP_MANIFEST = Path(__file__).resolve().parents[1] / "adtof-database-bootstrap-job.yaml"
FUNCTION_SIGNATURE = "clouddsp_complete_adtof_task(\n                uuid, uuid, text, text, text, uuid, text, text, jsonb\n              )"


class ADTOFCompletionFunctionManifestTests(unittest.TestCase):
    """Keep the worker's only Job-write capability typed and least-privileged."""

    def test_bootstrap_defines_and_limits_the_adtof_completion_function(self) -> None:
        """The worker gets function execution, never a broad Job UPDATE grant."""

        manifest = BOOTSTRAP_MANIFEST.read_text(encoding="utf-8")

        self.assertIn("CREATE OR REPLACE FUNCTION public.clouddsp_complete_adtof_task(", manifest)
        self.assertIn("SECURITY DEFINER", manifest)
        self.assertIn("SET search_path = pg_catalog", manifest)
        self.assertIn("FOR UPDATE OF task, job", manifest)
        self.assertIn("task.lease_expires_at > CURRENT_TIMESTAMP", manifest)
        self.assertIn("NOT (job.midi ? 'drums')", manifest)
        self.assertIn("REVOKE ALL PRIVILEGES ON FUNCTION public." + FUNCTION_SIGNATURE, manifest)
        self.assertIn("FROM PUBLIC;", manifest)
        self.assertIn("GRANT EXECUTE ON FUNCTION public." + FUNCTION_SIGNATURE, manifest)
        self.assertIn("TO :\"adtof_db_username\";", manifest)
        self.assertIn("AS can_complete_adtof_task", manifest)
        self.assertNotIn("GRANT UPDATE ON TABLE public.jobs", manifest)


if __name__ == "__main__":
    unittest.main()
