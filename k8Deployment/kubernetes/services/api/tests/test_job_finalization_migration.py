"""Static contract tests for durable, least-privilege parent-Job completion.

These tests do not connect to PostgreSQL or apply a migration. They keep the
versioned SQL and its one-shot Kubernetes Job aligned with the worker output
contract until the operator applies the migration in the local cluster.
"""

from __future__ import annotations

from pathlib import Path
import unittest


_SERVICE_DIRECTORY = Path(__file__).resolve().parents[1]
_CONFIG_MAP = _SERVICE_DIRECTORY / "job-api-schema-migration-v007-job-finalization-configmap.yaml"
_JOB = _SERVICE_DIRECTORY / "job-api-schema-migration-v007-job-finalization-job.yaml"
_CONFIG_MAP_V008 = _SERVICE_DIRECTORY / "job-api-schema-migration-v008-partial-task-finalization-configmap.yaml"
_JOB_V008 = _SERVICE_DIRECTORY / "job-api-schema-migration-v008-partial-task-finalization-job.yaml"


class JobFinalizationMigrationTests(unittest.TestCase):
    """Protect the durable output map, aggregate rule, and one-shot migration wiring."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config_map = _CONFIG_MAP.read_text(encoding="utf-8")
        cls.job = _JOB.read_text(encoding="utf-8")
        cls.config_map_v008 = _CONFIG_MAP_V008.read_text(encoding="utf-8")
        cls.job_v008 = _JOB_V008.read_text(encoding="utf-8")

    def test_migration_is_immutable_versioned_and_serialized(self) -> None:
        """Schema changes have their own source, Job, and ledger identity."""

        self.assertIn("kind: ConfigMap", self.config_map)
        self.assertIn("immutable: true", self.config_map)
        self.assertIn("007_job_finalization.sql: |", self.config_map)
        self.assertIn("pg_advisory_xact_lock(834929517101)", self.config_map)
        self.assertIn("'v006_adtof_processing_tasks'", self.config_map)
        self.assertIn("'v007_job_finalization'", self.config_map)
        self.assertIn("kind: Job", self.job)
        self.assertIn("restartPolicy: Never", self.job)
        self.assertIn("backoffLimit: 0", self.job)
        self.assertIn("automountServiceAccountToken: false", self.job)
        self.assertIn("name: job-api-schema-migration-v007-job-finalization", self.job)

    def test_basic_pitch_completion_is_an_exact_typed_capability(self) -> None:
        """The worker gets a function grant, not direct Job-row mutation rights."""

        self.assertIn("CREATE OR REPLACE FUNCTION public.clouddsp_complete_basic_pitch_task(", self.config_map)
        self.assertIn("SECURITY DEFINER", self.config_map)
        self.assertIn("SET search_path = pg_catalog", self.config_map)
        self.assertIn("task.lease_token = p_lease_token", self.config_map)
        self.assertIn("task.lease_expires_at > CURRENT_TIMESTAMP", self.config_map)
        self.assertIn("p_midi_sha256 !~ '^[0-9a-f]{64}$'", self.config_map)
        self.assertIn("'size_bytes', p_midi_size_bytes", self.config_map)
        self.assertIn("'sha256', p_midi_sha256", self.config_map)
        self.assertIn("TO \"clouddsp-basic-pitch\"", self.config_map)

    def test_deferred_aggregate_requires_all_expected_tasks_and_registered_outputs(self) -> None:
        """A Job reaches a terminal state only after its full stage contract resolves."""

        self.assertIn("CREATE OR REPLACE FUNCTION public.clouddsp_finalize_midi_processing_job(", self.config_map)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", self.config_map)
        self.assertIn("NEW.status IN ('succeeded', 'failed')", self.config_map)
        for mode in ("2-stems", "4-stems", "6-stems"):
            self.assertIn(f"WHEN '{mode}'", self.config_map)
        self.assertIn("nonterminal_task_count > 0", self.config_map)
        self.assertIn("failed_task_count > 0", self.config_map)
        self.assertIn("'One or more audio-processing steps failed.'", self.config_map)
        self.assertIn("'The processing pipeline did not register every output artifact.'", self.config_map)
        self.assertIn("SET status = 'completed'", self.config_map)
        self.assertIn("FOR existing_job IN", self.config_map)

    def test_migration_job_uses_only_schema_owner_secret_and_read_only_sql_projection(self) -> None:
        """Migration wiring does not copy administrator or worker credentials."""

        self.assertIn("clouddsp-job-api-database-credentials", self.job)
        self.assertNotIn("clouddsp-postgresql-credentials", self.job)
        self.assertNotIn("clouddsp-basic-pitch-database-credentials", self.job)
        self.assertIn("/migrations/007_job_finalization.sql", self.job)
        self.assertIn("readOnly: true", self.job)

    def test_v008_waits_for_lazily_registered_downstream_tasks(self) -> None:
        """Queue dispatch can create downstream task rows after early stems finish."""

        self.assertIn("'v007_job_finalization'", self.config_map_v008)
        self.assertIn("'v008_partial_task_finalization'", self.config_map_v008)
        self.assertIn("downstream_task_count > expected_count", self.config_map_v008)
        self.assertIn("matched_task_count <> downstream_task_count", self.config_map_v008)
        partial_task_branch = self.config_map_v008.split(
            "IF downstream_task_count < expected_count THEN", 1
        )[1]
        self.assertIn("RETURN 'waiting';", partial_task_branch.split("END IF;", 1)[0])
        self.assertIn("IF failed_task_count > 0 THEN", self.config_map_v008)
        self.assertIn("name: job-api-schema-migration-v008-partial-task-finalization", self.job_v008)
        self.assertIn("clouddsp-job-api-database-credentials", self.job_v008)


if __name__ == "__main__":  # pragma: no cover - directly runnable learning aid.
    unittest.main()
