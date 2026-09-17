"""Unit tests for the source-only ADTOF worker-smoke fixture.

These tests use only the Python standard library. They do not upload an object,
open PostgreSQL/RabbitMQ/MinIO, run an ADTOF model, build an image, or create a
Kubernetes resource.
"""

from __future__ import annotations

import hashlib
import io
import unittest
import wave
from uuid import UUID

from adtof_worker_smoke_fixture import (
    FIXED_OBJECT_KEYS,
    MAX_SMOKE_WAV_BYTES,
    MIDI_KEY,
    SMOKE_EVENT_ID,
    SMOKE_JOB_ID,
    STEM_KEY,
    STEM_MODE,
    STEM_NAME,
    SYNTHETIC_DEMUCS_TASK_ID,
    TEMPO_KEY,
    WAV_CHANNEL_COUNT,
    WAV_DURATION_SECONDS,
    WAV_SAMPLE_RATE,
    build_controlled_drum_fixture,
    build_controlled_drum_wav,
    controlled_stem_metadata,
)


class ADTOFWorkerSmokeFixtureTests(unittest.TestCase):
    """Keep the future integration fixture exact, bounded, and reproducible."""

    def test_fixed_coordinates_are_canonical_and_limit_storage_to_three_objects(self) -> None:
        """A later smoke identity must not expand from these reserved paths."""

        for identifier in (SMOKE_JOB_ID, SMOKE_EVENT_ID, SYNTHETIC_DEMUCS_TASK_ID):
            self.assertEqual(str(UUID(identifier)), identifier)
        self.assertEqual(STEM_NAME, "drums")
        self.assertEqual(STEM_MODE, "4-stems")
        self.assertEqual(
            FIXED_OBJECT_KEYS,
            frozenset({STEM_KEY, MIDI_KEY, TEMPO_KEY}),
        )
        self.assertEqual(STEM_KEY, f"stems/{SMOKE_JOB_ID}/drums.wav")
        self.assertEqual(MIDI_KEY, f"midi/{SMOKE_JOB_ID}/drums.mid")
        self.assertEqual(TEMPO_KEY, f"midi/{SMOKE_JOB_ID}/drums_bpm.json")

    def test_controlled_wav_is_repeatable_pcm_audio_with_the_reviewed_duration(self) -> None:
        """The fixture cannot become random bytes, user input, or an oversized file."""

        first = build_controlled_drum_wav()
        self.assertEqual(first, build_controlled_drum_wav())
        self.assertTrue(44 <= len(first) <= MAX_SMOKE_WAV_BYTES)
        self.assertEqual(first[:4], b"RIFF")
        self.assertEqual(first[8:12], b"WAVE")
        with wave.open(io.BytesIO(first), "rb") as reader:
            self.assertEqual(reader.getnchannels(), WAV_CHANNEL_COUNT)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertEqual(reader.getframerate(), WAV_SAMPLE_RATE)
            self.assertEqual(reader.getnframes(), WAV_SAMPLE_RATE * WAV_DURATION_SECONDS)
            self.assertNotEqual(reader.readframes(reader.getnframes()), b"\x00" * (len(first) - 44))

    def test_fixture_evidence_and_metadata_match_the_generated_bytes_exactly(self) -> None:
        """The future durable request cannot disagree with the uploaded stem."""

        fixture = build_controlled_drum_fixture()
        self.assertEqual(fixture.size_bytes, len(fixture.wav_bytes))
        self.assertEqual(fixture.sha256, hashlib.sha256(fixture.wav_bytes).hexdigest())
        self.assertEqual(
            fixture.metadata,
            controlled_stem_metadata(size_bytes=fixture.size_bytes, sha256=fixture.sha256),
        )
        self.assertEqual(
            set(fixture.metadata),
            {
                "schema-version",
                "producer",
                "job-id",
                "task-id",
                "stem-name",
                "stem-mode",
                "size-bytes",
                "sha256",
            },
        )
        self.assertEqual(fixture.metadata["job-id"], SMOKE_JOB_ID)
        self.assertEqual(fixture.metadata["task-id"], SYNTHETIC_DEMUCS_TASK_ID)
        self.assertEqual(fixture.metadata["stem-name"], STEM_NAME)
        self.assertEqual(fixture.metadata["stem-mode"], STEM_MODE)

    def test_metadata_rejects_invalid_size_or_digest_before_a_future_upload(self) -> None:
        """Bad evidence must not become a database event or MinIO metadata value."""

        with self.assertRaises(ValueError):
            controlled_stem_metadata(size_bytes=0, sha256="a" * 64)
        with self.assertRaises(ValueError):
            controlled_stem_metadata(size_bytes=MAX_SMOKE_WAV_BYTES + 1, sha256="a" * 64)
        with self.assertRaises(ValueError):
            controlled_stem_metadata(size_bytes=1, sha256="not-a-sha256")


if __name__ == "__main__":
    unittest.main()
