"""The queue boundary rejects malformed or cross-feature notifications."""

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from app.event import sheet_candidates
from app.render import InvalidScore, validate_midi
from app.state import Claim, claim, complete
from app.worker import _handle, _process_candidate

JOB = "2fbd8181-3068-4e77-b46a-e0a9d667b2a7"


class ScoreWorkerTests(unittest.TestCase):
    def event(self, key, bucket="clouddsp-uploads"):
        return json.dumps({"Records": [{"eventName": "s3:ObjectCreated:Post",
            "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}).encode()

    def test_only_canonical_score_input_enters_worker(self):
        key = f"midi-sheet-inputs/{JOB}/source.mid"
        self.assertEqual(sheet_candidates(self.event(key)), [(JOB, key)])
        for rejected in (f"uploads/{JOB}/source.mid", f"midi-sheet-results/{JOB}/result.pdf",
                         f"midi-sheet-inputs/{JOB}/other.png", "midi-sheet-inputs/bad/source.mid"):
            self.assertEqual(sheet_candidates(self.event(rejected)), [])
        self.assertEqual(sheet_candidates(self.event(key, "other-bucket")), [])
        self.assertEqual(sheet_candidates(b"not-json"), [])

    def test_invalid_midi_rejected_before_engraving(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.mid"
            for data in (b"not-midi", b"MThd\x00"):
                source.write_bytes(data)
                with self.assertRaises(InvalidScore): validate_midi(source)

    def handle(self, channel, headers=None):
        _handle(Mock(), channel, SimpleNamespace(delivery_tag=7),
                SimpleNamespace(headers=headers), self.event(f"midi-sheet-inputs/{JOB}/source.mid"), Mock())

    def test_ack_happens_after_durable_processing(self):
        channel = Mock()
        events = []
        channel.basic_ack.side_effect = lambda *args: events.append("ack")
        with patch("app.worker._process_candidate", side_effect=lambda *args: events.append("durable")):
            self.handle(channel)
        self.assertEqual(events, ["durable", "ack"])

    def test_retry_must_be_confirmed_before_ack(self):
        channel = Mock()
        events = []
        channel.basic_publish.side_effect = lambda **kwargs: events.append("confirmed")
        channel.basic_ack.side_effect = lambda *args: events.append("ack")
        with patch("app.worker._process_candidate", side_effect=ConnectionError):
            self.handle(channel)
        self.assertEqual(events, ["confirmed", "ack"])
        self.assertEqual(channel.basic_publish.call_args.kwargs["properties"].headers, {"x-sheet-attempt": 1})

    def test_lost_publish_confirmation_never_acknowledges_source(self):
        channel = Mock()
        channel.basic_publish.side_effect = ConnectionError
        with patch("app.worker._process_candidate", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.handle(channel)
        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(7, requeue=True)

    def test_database_outage_on_exhaustion_retains_delivery_in_dlq(self):
        channel = Mock()
        with patch("app.worker._process_candidate", side_effect=ConnectionError), \
                patch("app.worker._db", side_effect=ConnectionError):
            self.handle(channel, {"x-sheet-attempt": 2})
        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(7, requeue=False)

    def test_terminal_duplicate_does_no_storage_or_inference_work(self):
        storage = Mock()
        with patch("app.worker._db") as db, patch("app.worker.read_source", return_value=(
                "clouddsp-uploads", "key", "audio/midi", 100, "completed")), \
                patch("app.worker.render") as model:
            _process_candidate(Mock(), storage, JOB, "key")
        storage.head_object.assert_not_called()
        model.assert_not_called()

    def test_new_pod_cannot_reset_durable_attempt_limit(self):
        db = MagicMock()
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("clouddsp-uploads", "key", "audio/midi", 100,
                                       "source_uploaded", None, 3)
        self.assertEqual(claim(db, JOB, "key"), Claim("terminal"))
        sql = cursor.execute.call_args.args[0]
        self.assertIn("status = 'failed'", sql)

    def test_stale_completion_is_rejected(self):
        db = MagicMock()
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        self.assertFalse(complete(db, JOB, "old-token", bucket="clouddsp-uploads",
                                  pdf_key="midi", xml_key="xml"))
        self.assertIn("lease_token = %s", cursor.execute.call_args.args[0])
        self.assertIn("lease_expires_at > CURRENT_TIMESTAMP", cursor.execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
