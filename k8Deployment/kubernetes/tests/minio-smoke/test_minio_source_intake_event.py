"""Unit tests for the pure MinIO-event matcher used by the transport smoke test."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


# The source client will later be copied into its own immutable container image.
# Add its source directory explicitly so these lightweight unit tests remain
# runnable on the host without building the image or installing Pika.
CLIENT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "rabbitmq"
    / "minio-source-intake-smoke-client"
)
sys.path.insert(0, str(CLIENT_SOURCE))

from minio_source_intake_event import (  # noqa: E402 - path setup is intentional above.
    ExpectedSourceEvent,
    SourceIntakeSmokeEventError,
    assert_expected_source_event,
)


EXPECTED = ExpectedSourceEvent(
    bucket_name="clouddsp-uploads",
    object_key="uploads/_verification/minio-source-intake-smoke.txt",
)


def event_body(*, event_name: str = "s3:ObjectCreated:Put", bucket: str = "clouddsp-uploads", key: str = "uploads%2F_verification%2Fminio-source-intake-smoke.txt", records: int = 1) -> bytes:
    """Return a small S3-compatible envelope with the requested number of records."""

    record = {
        "eventName": event_name,
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": key},
        },
    }
    return json.dumps({"Records": [record for _ in range(records)]}).encode("utf-8")


class SourceIntakeSmokeEventTests(unittest.TestCase):
    """Prove the transport test cannot acknowledge a different event by mistake."""

    def test_accepts_one_expected_put_event_and_decodes_its_key_once(self) -> None:
        assert_expected_source_event(event_body(), EXPECTED)

    def test_rejects_post_event_because_this_transport_upload_is_a_put(self) -> None:
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(event_body(event_name="s3:ObjectCreated:Post"), EXPECTED)

    def test_rejects_a_different_bucket_or_object_key(self) -> None:
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(event_body(bucket="unrelated-bucket"), EXPECTED)
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(event_body(key="uploads%2Fother.txt"), EXPECTED)

    def test_rejects_malformed_or_ambiguous_event_envelopes(self) -> None:
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(b"not-json", EXPECTED)
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(event_body(key="uploads%2Gbad"), EXPECTED)
        with self.assertRaises(SourceIntakeSmokeEventError):
            assert_expected_source_event(event_body(records=2), EXPECTED)


if __name__ == "__main__":
    unittest.main()
