"""Offline contract tests for the load broker's fixed PostgreSQL target."""

from __future__ import annotations

import unittest

from postgresql_admin_boundary import (
    PostgreSQLAdminBoundaryError,
    PostgreSQLAdminConnectionSettings,
)


class PostgreSQLAdminBoundaryTests(unittest.TestCase):
    """Keep privileged observer setup on the actual Job API database only."""

    def test_accepts_the_database_used_by_the_job_api_and_migrations(self) -> None:
        """The observer must see the same public.jobs relation as the API."""

        settings = PostgreSQLAdminConnectionSettings(
            host="clouddsp-postgresql.clouddsp-data.svc",
            port=5432,
            database="clouddsp_job_api",
            username="clouddsp-admin",
            password="not-a-real-password",
        )

        self.assertEqual(settings.database, "clouddsp_job_api")

    def test_rejects_the_other_local_database_without_echoing_input(self) -> None:
        """A valid connection cannot silently point at the unrelated database."""

        with self.assertRaises(PostgreSQLAdminBoundaryError) as raised:
            PostgreSQLAdminConnectionSettings(
                host="clouddsp-postgresql.clouddsp-data.svc",
                port=5432,
                database="clouddsp",
                username="clouddsp-admin",
                password="not-a-real-password",
            )

        self.assertNotIn("clouddsp", str(raised.exception))


if __name__ == "__main__":  # pragma: no cover - direct local teaching aid.
    unittest.main()
