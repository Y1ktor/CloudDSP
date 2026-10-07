"""Focused score upload contract, database boundary, and S3 policy checks."""

from __future__ import annotations

import base64
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID

from pydantic import ValidationError

from app.authentication import AuthenticatedPrincipal
from app.database import (
    CREATE_SCORE_UPLOAD_PENDING_JOB_SQL,
    DIRECT_UPLOAD_RETENTION_DAYS,
    CreatedScoreUploadJob,
    DatabaseSettings,
    create_score_upload_pending_job,
)
from app.main import create_score_upload_job
from app.object_storage import ObjectStorageSettings
from app.presigned_upload import (
    PresignedUploadContractError,
    create_constrained_score_upload_post,
)
from app.score_upload_contract import MAX_SCORE_SOURCE_BYTES, ScoreUploadRequest


JOB_ID = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def storage() -> ObjectStorageSettings:
    return ObjectStorageSettings(
        internal_endpoint="http://minio.test.svc:9000",
        public_endpoint="http://minio.localhost:8080",
        uploads_bucket="clouddsp-uploads",
        region="us-east-1",
        addressing_style="path",
        access_key="test-access-key",
        secret_key="test-secret-key",
    )


class ScoreUploadTests(unittest.TestCase):
    def test_allowlist_and_canonical_type(self) -> None:
        for name, reported, canonical in (
            ("score.pdf", "application/pdf", "application/pdf"),
            ("scan.PNG", "image/png", "image/png"),
            ("old.jpg", "image/jpg", "image/jpeg"),
            ("old.jpeg", None, "image/jpeg"),
        ):
            with self.subTest(name=name):
                request = ScoreUploadRequest(
                    direction="score_to_midi", filename=name,
                    content_type=reported, size_bytes=1024,
                )
                self.assertEqual(request.canonical_content_type, canonical)

    def test_rejects_unsafe_or_unsupported_input(self) -> None:
        base = {"direction": "score_to_midi", "filename": "scan.png", "size_bytes": 1}
        for changes in (
            {"direction": "midi_to_score"},
            {"filename": "../scan.png"},
            {"filename": "folder\\scan.png"},
            {"filename": "score.svg"},
            {"content_type": "application/pdf"},
            {"size_bytes": 0},
            {"size_bytes": MAX_SCORE_SOURCE_BYTES + 1},
            {"owner_sub": "attacker"},
            {"input_object_key": "score-inputs/other/source.pdf"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ScoreUploadRequest(**(base | changes))

    @patch("app.database.uuid4", return_value=UUID(JOB_ID))
    @patch("app.database.DatabaseSettings.from_environment")
    @patch("app.database.psycopg.connect")
    def test_database_insert_binds_owner_and_server_key(self, connect, from_environment, _uuid4) -> None:
        from_environment.return_value = DatabaseSettings(
            host="postgres.test", port=5432, database="clouddsp_job_api",
            username="test", password="test",
        )
        connection = MagicMock()
        connect.return_value.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        expiry = NOW + timedelta(days=DIRECT_UPLOAD_RETENTION_DAYS)
        cursor.fetchone.return_value = {
            "job_id": JOB_ID, "direction": "score_to_midi", "status": "upload_pending",
            "revision": 1, "expires_at": expiry,
        }
        request = ScoreUploadRequest(
            direction="score_to_midi", filename="old.jpg", size_bytes=1234,
        )
        created = create_score_upload_pending_job(
            owner_sub="verified-sub", request=request,
            input_bucket="clouddsp-uploads", now=NOW,
        )
        self.assertEqual(created.input_object_key, f"score-inputs/{JOB_ID}/source.jpg")
        connection.transaction.assert_called_once_with()
        cursor.execute.assert_called_once_with(
            CREATE_SCORE_UPLOAD_PENDING_JOB_SQL,
            (JOB_ID, "verified-sub", "clouddsp-uploads", created.input_object_key,
             "old.jpg", "image/jpeg", 1234, expiry),
        )

    def test_signer_restricts_key_metadata_type_size_and_expiry(self) -> None:
        key = f"score-inputs/{JOB_ID}/source.pdf"
        form = create_constrained_score_upload_post(
            storage(), job_id=JOB_ID, input_object_key=key,
            content_type="application/pdf",
        )
        self.assertEqual(form.fields["key"], key)
        self.assertEqual(form.fields["x-amz-meta-score-direction"], "score_to_midi")
        self.assertEqual(form.maximum_source_bytes, MAX_SCORE_SOURCE_BYTES)
        policy = json.loads(base64.b64decode(form.fields["policy"]))
        self.assertIn(["content-length-range", 1, MAX_SCORE_SOURCE_BYTES], policy["conditions"])
        self.assertIn({"key": key}, policy["conditions"])
        self.assertIn({"Content-Type": "application/pdf"}, policy["conditions"])
        self.assertIn({"x-amz-meta-job-id": JOB_ID}, policy["conditions"])
        self.assertIn({"x-amz-meta-score-direction": "score_to_midi"}, policy["conditions"])
        for wrong_key, wrong_type in (
            (f"score-inputs/{JOB_ID}/other.pdf", "application/pdf"),
            (f"score-inputs/{JOB_ID}/source.jpg", "application/pdf"),
            ("uploads/" + JOB_ID + "/score.pdf", "application/pdf"),
        ):
            with self.assertRaises(PresignedUploadContractError):
                create_constrained_score_upload_post(
                    storage(), job_id=JOB_ID, input_object_key=wrong_key,
                    content_type=wrong_type,
                )

    def test_route_commits_before_signing_and_hides_storage_coordinates(self) -> None:
        request = ScoreUploadRequest(
            direction="score_to_midi", filename="old.jpg", size_bytes=1234,
        )
        created = CreatedScoreUploadJob(
            job_id=JOB_ID, input_object_key=f"score-inputs/{JOB_ID}/source.jpg",
            direction="score_to_midi", status="upload_pending", revision=1,
            expires_at=NOW + timedelta(days=14),
        )
        order = []

        def persist(**_kwargs):
            order.append("database")
            return created

        def sign(*_args, **kwargs):
            order.append("sign")
            return create_constrained_score_upload_post(storage(), **kwargs)

        with (
            patch("app.main.ObjectStorageSettings.from_environment", return_value=storage()),
            patch("app.main.create_score_upload_pending_job", side_effect=persist),
            patch("app.main.create_constrained_score_upload_post", side_effect=sign),
        ):
            response = create_score_upload_job(request, AuthenticatedPrincipal(subject="verified-sub"))
        payload = json.loads(response.body)
        self.assertEqual(order, ["database", "sign"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("input_bucket", payload)
        self.assertNotIn("input_object_key", payload)
        self.assertEqual(payload["max_source_bytes"], MAX_SCORE_SOURCE_BYTES)
