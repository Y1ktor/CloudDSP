"""Owner-bound score status exposes only deterministic completed artifacts."""

import json
import unittest
from unittest.mock import patch
from uuid import UUID

from app.authentication import AuthenticatedPrincipal
from app.sheet_routes import get_sheet_job, sheet_job_snapshot_response
from app.presigned_download import expected_download_key

JOB = "2fbd8181-3068-4e77-b46a-e0a9d667b2a7"


class ScoreStatusTests(unittest.TestCase):
    def test_result_keys_cannot_cross_job(self):
        self.assertEqual(expected_download_key(job_id=JOB, kind="sheet-pdf"),
                         f"midi-sheet-results/{JOB}/result.pdf")
        self.assertEqual(expected_download_key(job_id=JOB, kind="sheet-musicxml"),
                         f"midi-sheet-results/{JOB}/result.musicxml")

    def test_processing_snapshot_has_no_storage_coordinates(self):
        response = sheet_job_snapshot_response({
            "job_id": JOB, "status": "processing", "attempt_count": 1,
            "_result_bucket": None, "_result_pdf_key": None,
            "_result_musicxml_key": None,
        })
        payload = json.loads(response.body)
        self.assertNotIn("_result_bucket", payload)
        self.assertNotIn("pdf_url", payload)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_completed_snapshot_signs_only_own_result_keys(self):
        row = {"job_id": JOB, "status": "completed",
               "_result_bucket": "clouddsp-uploads",
               "_result_pdf_key": f"midi-sheet-results/{JOB}/result.pdf",
               "_result_musicxml_key": f"midi-sheet-results/{JOB}/result.musicxml"}
        with (
            patch("app.sheet_routes.ObjectStorageSettings.from_environment", return_value=type(
                "Settings", (), {"uploads_bucket": "clouddsp-uploads"})()),
            patch("app.sheet_routes.create_presigned_download_url", side_effect=["https://midi", "https://xml"]) as sign,
        ):
            response = sheet_job_snapshot_response(row)
        payload = json.loads(response.body)
        self.assertEqual(payload["pdf_url"], "https://midi")
        self.assertEqual(payload["musicxml_url"], "https://xml")
        self.assertNotIn("_result_pdf_key", payload)
        self.assertEqual([call.kwargs["kind"] for call in sign.call_args_list],
                         ["sheet-pdf", "sheet-musicxml"])

    def test_foreign_score_shares_missing_response(self):
        with patch("app.sheet_routes.get_retained_sheet_job_for_owner", return_value=None) as get:
            response = get_sheet_job(UUID(JOB), AuthenticatedPrincipal(subject="verified-owner"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(get.call_args.kwargs["owner_sub"], "verified-owner")
