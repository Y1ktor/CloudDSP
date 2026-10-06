"""Unit tests for pure, bounded FFprobe JSON interpretation.

These tests use captured-shaped JSON only.  They never start FFprobe, download
an object, access MinIO/PostgreSQL/RabbitMQ, allocate Demucs, or use Kubernetes.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.processing.audio_probe import (
    MAX_FFPROBE_OUTPUT_BYTES,
    DemucsAudioProbeFailureCode,
    DemucsAudioProbeProtocolError,
    DemucsPermanentAudioProbeError,
    parse_verified_demucs_audio_probe,
)


def probe_json(*, duration: str = "123.456", streams: str = '[{"codec_type":"audio"}]') -> bytes:
    """Return compact, ordinary FFprobe ``-of json``-shaped output bytes."""

    return f'{{"format":{{"duration":"{duration}"}},"streams":{streams}}}'.encode("utf-8")


class DemucsAudioProbeTests(unittest.TestCase):
    """Prove only short sources with declared audio streams reach Demucs."""

    def test_valid_audio_probe_returns_exact_duration_and_audio_stream_count(self) -> None:
        """A video stream is allowed when the container also has audio."""

        verified = parse_verified_demucs_audio_probe(
            probe_json(streams='[{"codec_type":"video"},{"codec_type":"audio"},{"codec_type":"audio"}]')
        )

        self.assertEqual(verified.duration_seconds, Decimal("123.456"))
        self.assertEqual(verified.audio_stream_count, 2)

    def test_exact_duration_limit_and_json_numeric_duration_are_accepted(self) -> None:
        """The exact 500-second boundary is valid without float rounding drift."""

        verified = parse_verified_demucs_audio_probe(
            b'{"format":{"duration":500},"streams":[{"codec_type":"audio"}]}'
        )

        self.assertEqual(verified.duration_seconds, Decimal("500"))

    def test_no_audio_stream_has_a_bounded_permanent_category(self) -> None:
        """A video-only valid FFprobe result is unsuitable media, not a retry."""

        with self.assertRaises(DemucsPermanentAudioProbeError) as raised:
            parse_verified_demucs_audio_probe(probe_json(streams='[{"codec_type":"video"}]'))

        self.assertEqual(raised.exception.failure_code, DemucsAudioProbeFailureCode.NO_AUDIO_STREAM)

    def test_invalid_or_unprovable_duration_is_permanent_source_rejection(self) -> None:
        """Unknown, non-positive, and non-finite lengths cannot pass the 500s rule."""

        cases = (
            probe_json(duration="N/A"),
            probe_json(duration="0"),
            probe_json(duration="-1"),
            probe_json(duration="NaN"),
            b'{"format":{},"streams":[{"codec_type":"audio"}]}',
        )
        for output in cases:
            with self.subTest(output=output):
                with self.assertRaises(DemucsPermanentAudioProbeError) as raised:
                    parse_verified_demucs_audio_probe(output)
                self.assertEqual(raised.exception.failure_code, DemucsAudioProbeFailureCode.INVALID_DURATION)

    def test_duration_over_the_limit_has_its_own_permanent_category(self) -> None:
        """The worker can persist a stable reason without exposing FFprobe text."""

        with self.assertRaises(DemucsPermanentAudioProbeError) as raised:
            parse_verified_demucs_audio_probe(probe_json(duration="500.000001"))

        self.assertEqual(
            raised.exception.failure_code,
            DemucsAudioProbeFailureCode.DURATION_LIMIT_EXCEEDED,
        )

    def test_malformed_ffprobe_output_is_a_protocol_error_not_media_evidence(self) -> None:
        """The future process adapter can retry or diagnose malformed tool output safely."""

        malformed_outputs = (
            b"not-json",
            b"[]",
            b'{"format":{"duration":"10"},"streams":"not-a-list"}',
            b'{"format":{"duration":"10"},"streams":[{"codec_type":true}]}',
            b'{"format":{"duration":"10","duration":"20"},"streams":[{"codec_type":"audio"}]}',
            b"\xff",
            b"x" * (MAX_FFPROBE_OUTPUT_BYTES + 1),
        )
        for output in malformed_outputs:
            with self.subTest(output_size=len(output)):
                with self.assertRaises(DemucsAudioProbeProtocolError):
                    parse_verified_demucs_audio_probe(output)


if __name__ == "__main__":
    unittest.main()
