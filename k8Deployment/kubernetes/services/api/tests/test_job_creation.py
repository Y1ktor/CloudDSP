"""Unit tests for the one-transaction upload-pending PostgreSQL helper.

All database connections are mocks. These tests prove SQL parameters and
transaction settings without contacting PostgreSQL, Keycloak, MinIO, RabbitMQ,
Docker, or Kubernetes. A later focused integration test will use the real
ClusterIP Service after the HTTP route has been deliberately added.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.database import (
    CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL,
    DIRECT_UPLOAD_RETENTION_DAYS,
    DatabaseSettings,
    DatabaseUnavailable,
    create_direct_upload_pending_job,
)
from app.direct_upload_contract import DirectUploadJobRequest


FIXED_JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
FIXED_EXPIRY = FIXED_NOW + timedelta(days=DIRECT_UPLOAD_RETENTION_DAYS)


class CreateDirectUploadPendingJobTests(unittest.TestCase):
    """Prove direct-upload persistence is owner-bound, atomic, and fail-closed."""

    def setUp(self) -> None:
        # No real Secret enters this test. The psycopg connect function is
        # mocked, so these values remain ordinary local Python test strings.
        self.settings = DatabaseSettings(
            host="postgresql.test",
            port=5432,
            database="clouddsp_job_api",
            username="clouddsp-job-api",
            password="not-a-real-password",
        )
        self.request = DirectUploadJobRequest(
            filename="mix.wav",
            content_type="audio/x-wav",
            size_bytes=1_024,
            stem_mode="4-stems",
        )

    @patch("app.database.uuid4", return_value=UUID(FIXED_JOB_ID))
    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_inserts_one_server_owned_upload_pending_row_in_one_transaction(
        self,
        connect,
        from_environment,
        uuid4,
    ) -> None:
        """The browser request becomes one owner-bound durable record before signing."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "job_id": FIXED_JOB_ID,
            "status": "upload_pending",
            "revision": 1,
            "expires_at": FIXED_EXPIRY,
        }

        created = create_direct_upload_pending_job(
            owner_sub="verified-keycloak-subject",
            request=self.request,
            input_bucket="clouddsp-uploads",
            now=FIXED_NOW,
        )

        self.assertEqual(created.job_id, FIXED_JOB_ID)
        self.assertEqual(created.input_object_key, f"uploads/{FIXED_JOB_ID}/mix.wav")
        self.assertEqual(created.status, "upload_pending")
        self.assertEqual(created.revision, 1)
        self.assertEqual(created.expires_at, FIXED_EXPIRY)
        uuid4.assert_called_once_with()
        connection.transaction.assert_called_once_with()
        cursor.execute.assert_called_once_with(
            CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL,
            (
                FIXED_JOB_ID,
                "verified-keycloak-subject",
                "clouddsp-uploads",
                f"uploads/{FIXED_JOB_ID}/mix.wav",
                "mix.wav",
                "audio/wav",
                1_024,
                "4-stems",
                FIXED_EXPIRY,
            ),
        )
        self.assertIn("INSERT INTO jobs", CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL)
        self.assertIn("'direct_upload'", CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL)
        self.assertIn("'upload_pending'", CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL)
        self.assertNotIn("source_uploaded", CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL)
        self.assertNotIn("DELETE", CREATE_DIRECT_UPLOAD_PENDING_JOB_SQL)

        connection_arguments = connect.call_args.kwargs
        self.assertEqual(connection_arguments["application_name"], "clouddsp-job-api-create-direct-upload")
        self.assertFalse(connection_arguments["autocommit"])
        self.assertNotIn("default_transaction_read_only=on", connection_arguments["options"])

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_driver_failure_becomes_one_safe_retryable_category(self, connect, from_environment) -> None:
        """A PostgreSQL error cannot disclose connection or SQL diagnostics."""

        from_environment.return_value = self.settings
        connect.side_effect = OSError("private database host diagnostic")

        with self.assertRaises(DatabaseUnavailable) as raised:
            create_direct_upload_pending_job(
                owner_sub="verified-keycloak-subject",
                request=self.request,
                input_bucket="clouddsp-uploads",
                now=FIXED_NOW,
            )

        self.assertEqual(str(raised.exception), "PostgreSQL job creation is unavailable.")
        self.assertNotIn("private database host diagnostic", str(raised.exception))

    @patch("app.database.DatabaseSettings.from_environment")
    def test_rejects_bad_internal_inputs_before_a_database_connection(self, from_environment) -> None:
        """A reusable database helper still protects its owner/bucket boundary."""

        invalid_arguments = (
            {"owner_sub": ""},
            {"owner_sub": "subject\x00with-nul"},
            {"input_bucket": ""},
            {"input_bucket": "bucket\x00with-nul"},
            {"now": datetime(2026, 9, 6, 12, 0)},
        )
        for overrides in invalid_arguments:
            with self.subTest(overrides=overrides):
                arguments = {
                    "owner_sub": "verified-keycloak-subject",
                    "request": self.request,
                    "input_bucket": "clouddsp-uploads",
                    "now": FIXED_NOW,
                }
                arguments.update(overrides)
                with self.assertRaises(ValueError):
                    create_direct_upload_pending_job(**arguments)

        from_environment.assert_not_called()

    @patch("app.database.uuid4", return_value=UUID(FIXED_JOB_ID))
    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_refuses_to_sign_after_an_invalid_returning_row(
        self,
        connect,
        from_environment,
        uuid4,
    ) -> None:
        """A malformed database result cannot become a future MinIO permission."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "job_id": FIXED_JOB_ID,
            "status": "upload_pending",
            "revision": 0,
            "expires_at": FIXED_EXPIRY,
        }

        with self.assertRaises(DatabaseUnavailable) as raised:
            create_direct_upload_pending_job(
                owner_sub="verified-keycloak-subject",
                request=self.request,
                input_bucket="clouddsp-uploads",
                now=FIXED_NOW,
            )

        self.assertEqual(
            str(raised.exception),
            "PostgreSQL job creation returned an invalid durable row.",
        )
        uuid4.assert_called_once_with()
