"""Focused unit tests for the first authenticated read-only Job API route.

The tests use only Python's standard-library ``unittest`` and mocks. They do
not need PostgreSQL, Keycloak, a browser token, Docker, or a Kubernetes cluster;
the later image/in-cluster task will prove those integration boundaries.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.authentication import AuthenticatedPrincipal
from app.object_storage import ObjectStorageSettings
from app.presigned_download import PresignedDownloadContractError
from app.database import (
    DatabaseSettings,
    DatabaseUnavailable,
    GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL,
    LIST_RETAINED_JOBS_FOR_OWNER_SQL,
    get_retained_job_snapshot_for_owner,
    list_retained_jobs_for_owner,
)
from app.main import (
    app,
    get_job_detail,
    job_history_unavailable_response,
    list_jobs,
    saved_jobs_response,
)


FIXED_JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"


def retained_snapshot_row() -> dict[str, object]:
    """Return one safe database-shaped detail row for isolated route tests."""

    return {
        "job_id": FIXED_JOB_ID,
        "source_type": "direct_upload",
        "source_filename": "song.wav",
        "source_content_type": "audio/wav",
        "source_size_bytes": 1_024,
        "source_uploaded": False,
        "stem_mode": "4-stems",
        "status": "upload_pending",
        "revision": 1,
        "stems": {},
        "midi": {},
        "tempo": None,
        "error": None,
        "created_at": datetime(2026, 9, 5, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 5, 1, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 12, tzinfo=UTC),
    }


class RetainedJobDatabaseQueryTests(unittest.TestCase):
    """Prove the SQL query binds one verified owner and has no write statement."""

    def setUp(self) -> None:
        # These test-only values never reach a real driver connection because
        # psycopg.connect is mocked in every database-query test below.
        self.settings = DatabaseSettings(
            host="postgresql.test",
            port=5432,
            database="clouddsp_job_api",
            username="clouddsp-job-api",
            password="not-a-real-password",
        )

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_query_binds_owner_and_returns_selected_rows(self, connect, from_environment) -> None:
        """A caller can receive its rows without SQL text interpolation or writes."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {
                "job_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                "source_filename": "song.wav",
                "status": "completed",
                "stem_mode": "4-stems",
                "tempo": {"bpm": 120},
                "created_at": datetime(2026, 9, 5, tzinfo=UTC),
                "updated_at": datetime(2026, 9, 5, 1, tzinfo=UTC),
                "expires_at": datetime(2026, 9, 12, tzinfo=UTC),
            }
        ]

        rows = list_retained_jobs_for_owner("verified-keycloak-subject")

        self.assertEqual(rows[0]["source_filename"], "song.wav")
        self.assertIn("WHERE owner_sub = %s", LIST_RETAINED_JOBS_FOR_OWNER_SQL)
        self.assertIn("expires_at > CURRENT_TIMESTAMP", LIST_RETAINED_JOBS_FOR_OWNER_SQL)
        self.assertNotIn("INSERT", LIST_RETAINED_JOBS_FOR_OWNER_SQL)
        self.assertNotIn("UPDATE", LIST_RETAINED_JOBS_FOR_OWNER_SQL)
        self.assertNotIn("DELETE", LIST_RETAINED_JOBS_FOR_OWNER_SQL)
        cursor.execute.assert_called_once_with(
            LIST_RETAINED_JOBS_FOR_OWNER_SQL,
            ("verified-keycloak-subject",),
        )
        self.assertEqual(connect.call_args.kwargs["application_name"], "clouddsp-job-api-list-jobs")
        self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_driver_error_becomes_safe_database_category(self, connect, from_environment) -> None:
        """Private connection details do not escape the database helper."""

        from_environment.return_value = self.settings
        connect.side_effect = OSError("private host diagnostic")

        with self.assertRaises(DatabaseUnavailable) as raised:
            list_retained_jobs_for_owner("verified-keycloak-subject")

        self.assertEqual(str(raised.exception), "PostgreSQL job history is unavailable.")


class RetainedJobDetailDatabaseQueryTests(unittest.TestCase):
    """Prove a detail lookup combines UUID, owner, expiry, and read-only SQL."""

    def setUp(self) -> None:
        self.settings = DatabaseSettings(
            host="postgresql.test",
            port=5432,
            database="clouddsp_job_api",
            username="clouddsp-job-api",
            password="not-a-real-password",
        )

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_detail_query_binds_id_and_verified_owner(self, connect, from_environment) -> None:
        """No object key or other user's row can be selected by this helper."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = retained_snapshot_row()

        row = get_retained_job_snapshot_for_owner(
            job_id=FIXED_JOB_ID,
            owner_sub="verified-keycloak-subject",
        )

        self.assertEqual(row["job_id"], FIXED_JOB_ID)
        self.assertIn("WHERE job_id = %s::uuid", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        self.assertIn("AND owner_sub = %s", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        self.assertIn("expires_at > CURRENT_TIMESTAMP", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        self.assertIn("input_object_key AS _storage_input_object_key", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        self.assertIn("input_bucket AS _storage_input_bucket", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        self.assertNotIn("UPDATE", GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL)
        cursor.execute.assert_called_once_with(
            GET_RETAINED_JOB_SNAPSHOT_FOR_OWNER_SQL,
            (FIXED_JOB_ID, "verified-keycloak-subject"),
        )
        self.assertEqual(connect.call_args.kwargs["application_name"], "clouddsp-job-api-get-job-detail")
        self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_missing_or_foreign_row_is_none_not_a_fake_snapshot(self, connect, from_environment) -> None:
        """The HTTP layer can map all non-visible rows to the same 404."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None

        self.assertIsNone(
            get_retained_job_snapshot_for_owner(
                job_id=FIXED_JOB_ID,
                owner_sub="verified-keycloak-subject",
            )
        )

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_unexpected_detail_row_fails_closed_not_as_a_404(self, connect, from_environment) -> None:
        """A driver/configuration regression must remain retryable server failure."""

        from_environment.return_value = self.settings
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = ("not-a-dict-row",)

        with self.assertRaises(DatabaseUnavailable) as raised:
            get_retained_job_snapshot_for_owner(
                job_id=FIXED_JOB_ID,
                owner_sub="verified-keycloak-subject",
            )

        self.assertEqual(str(raised.exception), "PostgreSQL job detail returned an invalid row.")

    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_driver_error_becomes_safe_detail_category(self, connect, from_environment) -> None:
        """A detail polling client never receives a private Psycopg exception."""

        from_environment.return_value = self.settings
        connect.side_effect = OSError("private host diagnostic")

        with self.assertRaises(DatabaseUnavailable) as raised:
            get_retained_job_snapshot_for_owner(
                job_id=FIXED_JOB_ID,
                owner_sub="verified-keycloak-subject",
            )

        self.assertEqual(str(raised.exception), "PostgreSQL job detail is unavailable.")


class SavedJobsResponseTests(unittest.TestCase):
    """Prove the HTTP boundary emits only compact browser-safe list items."""

    def test_response_serializes_database_timestamp_and_jsonb_values(self) -> None:
        """Psycopg values become plain JSON without exposing other row fields."""

        response = saved_jobs_response(
            [
                {
                    "job_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                    "source_filename": "song.wav",
                    "status": "completed",
                    "stem_mode": "4-stems",
                    "tempo": {"bpm": 120},
                    "created_at": datetime(2026, 9, 5, tzinfo=UTC),
                    "updated_at": datetime(2026, 9, 5, 1, tzinfo=UTC),
                    "expires_at": datetime(2026, 9, 12, tzinfo=UTC),
                }
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = json.loads(response.body)
        self.assertEqual(payload["jobs"][0]["job_id"], "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11")
        self.assertEqual(payload["jobs"][0]["tempo"], {"bpm": 120})
        self.assertEqual(payload["jobs"][0]["created_at"], "2026-09-05T00:00:00+00:00")

    def test_empty_job_history_is_a_successful_empty_array(self) -> None:
        """A new authenticated user has no error and no placeholder record."""

        response = saved_jobs_response([])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"jobs": []})

    def test_database_failure_is_generic_retryable_json(self) -> None:
        """The browser receives no database hostname, SQL, role, or exception text."""

        response = job_history_unavailable_response()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response.body), {"error": "Job history is temporarily unavailable."})


class ListJobsRouteTests(unittest.TestCase):
    """Prove the route delegates only the verified Keycloak subject to PostgreSQL."""

    def test_route_uses_verified_subject_and_returns_empty_history(self) -> None:
        """No browser-provided owner value is accepted by the route signature."""

        principal = AuthenticatedPrincipal(subject="verified-keycloak-subject")
        with patch("app.main.list_retained_jobs_for_owner", return_value=[]) as list_query:
            response = list_jobs(principal)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"jobs": []})
        list_query.assert_called_once_with("verified-keycloak-subject")

    def test_route_hides_database_failure(self) -> None:
        """A database outage does not reveal implementation details to the caller."""

        principal = AuthenticatedPrincipal(subject="verified-keycloak-subject")
        with patch(
            "app.main.list_retained_jobs_for_owner",
            side_effect=DatabaseUnavailable("private test detail"),
        ):
            response = list_jobs(principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"error": "Job history is temporarily unavailable."})

    def test_list_route_remains_registered_as_get(self) -> None:
        """Adding a separate POST route must not replace saved-job history."""

        methods = {
            frozenset(route.methods)
            for route in app.routes
            if getattr(route, "path", None) == "/jobs"
        }
        self.assertIn(frozenset({"GET"}), methods)


class JobDetailRouteTests(unittest.TestCase):
    """Prove the public detail path never trusts a browser owner field."""

    def setUp(self) -> None:
        self.principal = AuthenticatedPrincipal(subject="verified-keycloak-subject")

    def test_route_returns_one_owner_bound_upload_pending_snapshot(self) -> None:
        """A new upload can refresh without requiring MinIO or worker state."""

        with patch("app.main.get_retained_job_snapshot_for_owner", return_value=retained_snapshot_row()) as query:
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = json.loads(response.body)
        self.assertEqual(payload["job_id"], FIXED_JOB_ID)
        self.assertEqual(payload["status"], "upload_pending")
        self.assertFalse(payload["source_uploaded"])
        self.assertEqual(payload["stems"], {})
        self.assertEqual(payload["midi"], {})
        self.assertNotIn("input_bucket", payload)
        self.assertNotIn("input_object_key", payload)
        query.assert_called_once_with(
            job_id=FIXED_JOB_ID,
            owner_sub="verified-keycloak-subject",
        )

    def test_route_adds_fresh_urls_only_for_owner_checked_verified_artifacts(self) -> None:
        """The API removes private keys and hands the browser direct S3 URLs."""

        row = retained_snapshot_row()
        row.update(
            {
                "status": "midi_processing",
                "source_uploaded": True,
                "_storage_input_bucket": "clouddsp-uploads",
                "_storage_input_object_key": f"uploads/{FIXED_JOB_ID}/mix.wav",
                "stems": {
                    "vocals": {
                        "status": "ready",
                        "s3_key": f"stems/{FIXED_JOB_ID}/vocals.wav",
                        "bucket": "clouddsp-uploads",
                    }
                },
                "midi": {
                    "vocals": {
                        "status": "ready",
                        "s3_key": f"midi/{FIXED_JOB_ID}/vocals.mid",
                    },
                    "drums": {
                        "status": "ready",
                        "s3_key": f"midi/{FIXED_JOB_ID}/drums.mid",
                        "bpm_key": f"midi/{FIXED_JOB_ID}/drums_bpm.json",
                    },
                },
            }
        )
        settings = MagicMock(spec=ObjectStorageSettings)
        settings.uploads_bucket = "clouddsp-uploads"
        settings.public_endpoint = "http://minio.localhost:8080"

        def signed_url(_settings, *, kind: str, stem_name: str | None = None, **_kwargs) -> str:
            return f"https://private-test.invalid/{kind}/{stem_name or 'source'}"

        with (
            patch("app.main.get_retained_job_snapshot_for_owner", return_value=row),
            patch("app.main.ObjectStorageSettings.from_environment", return_value=settings),
            patch("app.main.create_presigned_download_url", side_effect=signed_url) as signer,
        ):
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["original_url"], "https://private-test.invalid/source/source")
        self.assertEqual(payload["stems"]["vocals"]["url"], "https://private-test.invalid/stem/vocals")
        self.assertEqual(payload["midi"]["vocals"]["url"], "https://private-test.invalid/midi/vocals")
        self.assertEqual(payload["midi"]["drums"]["url"], "https://private-test.invalid/midi/drums")
        self.assertEqual(payload["midi"]["drums"]["bpm_url"], "https://private-test.invalid/tempo/source")
        self.assertNotIn("_storage_input_bucket", payload)
        self.assertNotIn("_storage_input_object_key", payload)
        for artifact_map in (payload["stems"], payload["midi"]):
            for artifact in artifact_map.values():
                self.assertNotIn("s3_key", artifact)
                self.assertNotIn("bpm_key", artifact)
                self.assertNotIn("bucket", artifact)
        self.assertEqual(signer.call_count, 5)

    def test_missing_or_foreign_job_has_one_non_enumerating_404(self) -> None:
        """A guessed job UUID cannot distinguish absence from another owner."""

        with patch("app.main.get_retained_job_snapshot_for_owner", return_value=None):
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.body), {"error": "Job not found."})

    def test_demucs_timeout_is_a_clear_terminal_message_in_owner_snapshot(self) -> None:
        """The browser sees no private worker code or implied automatic retry."""

        row = retained_snapshot_row()
        row.update({"status": "failed", "error": "demucs_process_timed_out"})
        with patch("app.main.get_retained_job_snapshot_for_owner", return_value=row):
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("12-minute processing limit", payload["error"])
        self.assertIn("will not retry", payload["error"])
        self.assertNotIn("demucs_process_timed_out", payload["error"])

    def test_detail_database_failure_is_generic_and_retryable(self) -> None:
        """The route exposes no host, SQL, role, or exception detail."""

        with patch(
            "app.main.get_retained_job_snapshot_for_owner",
            side_effect=DatabaseUnavailable("private database detail"),
        ):
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {"error": "Job details are temporarily unavailable."},
        )

    def test_invalid_artifact_reference_is_a_generic_retryable_error(self) -> None:
        """A corrupt durable key never becomes a signed cross-Job download."""

        row = retained_snapshot_row()
        row.update(
            {
                "stems": {
                    "vocals": {
                        "status": "ready",
                        "s3_key": "stems/another-job/vocals.wav",
                    }
                }
            }
        )
        with (
            patch("app.main.get_retained_job_snapshot_for_owner", return_value=row),
            patch("app.main.ObjectStorageSettings.from_environment", return_value=MagicMock()),
            patch("app.main.create_presigned_download_url", side_effect=PresignedDownloadContractError("private")),
        ):
            response = get_job_detail(UUID(FIXED_JOB_ID), self.principal)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body),
            {"error": "Job details are temporarily unavailable."},
        )

    def test_detail_route_is_a_distinct_get_path(self) -> None:
        """The UUID route complements, rather than replacing, collection GET."""

        methods = {
            frozenset(route.methods)
            for route in app.routes
            if getattr(route, "path", None) == "/jobs/{job_id}"
        }
        self.assertIn(frozenset({"GET"}), methods)
