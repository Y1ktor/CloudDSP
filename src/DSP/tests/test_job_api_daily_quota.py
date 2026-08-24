"""Unit tests for atomic per-user UTC-day Job API quotas."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError


CLOUD_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "Cloud"
sys.path.insert(0, str(CLOUD_SOURCE_ROOT))

# job_api constructs boto3 clients at import time. No test may request instance
# metadata or use a real AWS account.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs")
os.environ.setdefault("DAILY_SUBMISSION_QUOTA_TABLE_NAME", "test-daily-quotas")

import job_api


class RecordingDynamoClient:
    def __init__(self, error: ClientError | None = None):
        self.error = error
        self.requests = []

    def transact_write_items(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error


class RecordingQuotaTable:
    def __init__(self, item=None):
        self.item = item or {}
        self.requests = []

    def get_item(self, **kwargs):
        self.requests.append(kwargs)
        return {"Item": self.item} if self.item else {}


class JobApiDailyQuotaTests(unittest.TestCase):
    def test_snapshot_reports_remaining_quota_and_next_utc_reset(self):
        quota_table = RecordingQuotaTable({"direct_upload_jobs": 2, "ytdlp_jobs": 3})
        with patch.object(job_api, "_daily_submission_quotas", quota_table), patch.object(
            job_api, "utc_day", return_value="2026-08-24"
        ), patch.object(job_api, "next_utc_reset_at", return_value="2026-08-25T00:00:00Z"):
            snapshot = job_api.daily_quota_snapshot("user-1")

        self.assertEqual(quota_table.requests[0]["Key"], {"quota_key": "user-1#2026-08-24"})
        self.assertTrue(quota_table.requests[0]["ConsistentRead"])
        self.assertEqual(snapshot["direct_uploads"], {"used": 2, "limit": 5, "remaining": 3})
        self.assertEqual(snapshot["ytdlp"], {"used": 3, "limit": 3, "remaining": 0})
        self.assertEqual(snapshot["resets_at"], "2026-08-25T00:00:00Z")

    def test_reserves_direct_slot_and_creates_job_in_one_transaction(self):
        client = RecordingDynamoClient()
        job = {"job_id": "e1b09580-d7a1-4e76-8705-911430c0b46b", "user_id": "user-1"}

        with patch.object(job_api, "_dynamodb_client", client), patch.object(
            job_api, "utc_day", return_value="2026-08-24"
        ):
            job_api.create_job_with_daily_quota(
                job=job,
                user_id="user-1",
                quota_attribute="direct_upload_jobs",
                maximum=5,
                quota_label="direct-upload job",
            )

        request = client.requests[0]["TransactItems"]
        quota_update, job_put = request
        self.assertEqual(quota_update["Update"]["TableName"], "test-daily-quotas")
        self.assertEqual(quota_update["Update"]["Key"]["quota_key"], {"S": "user-1#2026-08-24"})
        self.assertIn("#direct_upload_jobs < :maximum", quota_update["Update"]["ConditionExpression"])
        self.assertEqual(job_put["Put"]["TableName"], "test-jobs")
        self.assertEqual(job_put["Put"]["Item"]["job_id"], {"S": job["job_id"]})

    def test_rejects_a_quota_exhaustion_with_http_429(self):
        client = RecordingDynamoClient(
            ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "Cancelled"},
                    "CancellationReasons": [
                        {"Code": "ConditionalCheckFailed", "Message": "The conditional request failed"},
                        {"Code": "None", "Message": None},
                    ],
                },
                "TransactWriteItems",
            )
        )

        with patch.object(job_api, "_dynamodb_client", client), patch.object(
            job_api, "utc_day", return_value="2026-08-24"
        ):
            with self.assertRaises(job_api.RequestError) as raised:
                job_api.create_job_with_daily_quota(
                    job={"job_id": "07f5d209-4bb3-4c86-b15b-60b5c71b14bd"},
                    user_id="user-1",
                    quota_attribute="ytdlp_jobs",
                    maximum=3,
                    quota_label="yt-dlp job",
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Try again after 00:00 UTC", raised.exception.message)

    def test_submission_429_includes_the_current_quota_snapshot(self):
        quota = {
            "resets_at": "2026-08-25T00:00:00Z",
            "direct_uploads": {"used": 5, "limit": 5, "remaining": 0},
            "ytdlp": {"used": 0, "limit": 3, "remaining": 3},
        }
        with patch.object(job_api, "authenticated_user_id", return_value="user-1"), patch.object(
            job_api,
            "create_job",
            side_effect=job_api.RequestError(429, "Daily direct-upload job limit reached (5). Try again after 00:00 UTC."),
        ), patch.object(job_api, "append_daily_quota", return_value={"error": "limit", "quota": quota}):
            result = job_api.lambda_handler({"routeKey": "POST /jobs"}, None)

        self.assertEqual(result["statusCode"], 429)
        self.assertEqual(json.loads(result["body"])["quota"], quota)

    def test_distinct_utc_dates_use_distinct_quota_keys(self):
        client = RecordingDynamoClient()

        with patch.object(job_api, "_dynamodb_client", client), patch.object(
            job_api, "utc_day", side_effect=("2026-08-24", "2026-08-25")
        ):
            for index in range(2):
                job_api.create_job_with_daily_quota(
                    job={"job_id": f"job-{index}"},
                    user_id="user-1",
                    quota_attribute="direct_upload_jobs",
                    maximum=5,
                    quota_label="direct-upload job",
                )

        keys = [
            request["TransactItems"][0]["Update"]["Key"]["quota_key"]["S"]
            for request in client.requests
        ]
        self.assertEqual(keys, ["user-1#2026-08-24", "user-1#2026-08-25"])


if __name__ == "__main__":
    unittest.main()
