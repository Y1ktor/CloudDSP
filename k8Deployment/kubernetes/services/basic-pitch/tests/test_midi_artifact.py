"""Unit tests for local Basic Pitch MIDI framing and SHA-256 evidence.

Tests create small synthetic Standard MIDI Files in temporary worker scratch.
They do not run Basic Pitch, connect to MinIO/PostgreSQL/RabbitMQ, or interact
with Docker or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.processing.basic_pitch_process import BasicPitchInferenceCommand, build_basic_pitch_inference_command
from app.messaging.basic_pitch_requested_message import MIDI_CONTENT_TYPE
from app.artifacts.midi_artifact import (
    BasicPitchMidiArtifactContractError,
    BasicPitchMidiArtifactFormatError,
    BasicPitchMidiArtifactPathError,
    verify_and_hash_basic_pitch_midi,
)
from app.artifacts.stem_download import DownloadedBasicPitchStem
from app.db.stem_task_start import RunningBasicPitchStem
from app.db.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def standard_midi(*, track_count: int = 1, trailing: bytes = b"") -> bytes:
    """Build a small structurally valid type-1 SMF with a minimal end-of-track event."""

    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
    header += track_count.to_bytes(2, "big") + (120).to_bytes(2, "big")
    track = b"\x00\xff\x2f\x00"
    return header + (b"MTrk" + len(track).to_bytes(4, "big") + track) * track_count + trailing


def running_stem(source_path: Path) -> RunningBasicPitchStem:
    """Return committed-running evidence around one temporary generic WAV path."""

    lease = BasicPitchTaskLease(
        task_id=TASK_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        request_event_id=EVENT_ID,
        input_bucket="clouddsp-uploads",
        input_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
    )
    return RunningBasicPitchStem(
        lease=lease,
        stem=DownloadedBasicPitchStem(
            stem_path=source_path,
            size_bytes=101,
            sha256="a" * 64,
        ),
        started_at=datetime(2026, 9, 11, 12, 20, tzinfo=UTC),
    )


class BasicPitchMidiArtifactTests(unittest.TestCase):
    """Prove one completed model run cannot become arbitrary local-file evidence."""

    def _inference(self, temporary_root: Path) -> BasicPitchInferenceCommand:
        """Make the exact temporary layout produced by the preceding process boundary."""

        work_directory = temporary_root / "scratch"
        work_directory.mkdir()
        stem_directory = work_directory / "basic-pitch-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"the model input was verified in an earlier boundary")
        return build_basic_pitch_inference_command(
            running=running_stem(source_path),
            work_directory=work_directory,
        )

    def test_hashes_one_exact_structured_midi_output(self) -> None:
        """The future uploader receives only a fixed path, MIME type, length, and digest."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            contents = standard_midi()
            inference.expected_midi_path.write_bytes(contents)

            artifact = verify_and_hash_basic_pitch_midi(inference)

            self.assertEqual(artifact.path, inference.expected_midi_path)
            self.assertEqual(artifact.size_bytes, len(contents))
            self.assertEqual(artifact.sha256, hashlib.sha256(contents).hexdigest())
            self.assertEqual(artifact.content_type, MIDI_CONTENT_TYPE)

    def test_tampered_coordinate_or_symlink_never_becomes_local_evidence(self) -> None:
        """A hand-built dataclass cannot broaden the readable worker-scratch path."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inference = self._inference(root)
            outside = root / "outside.mid"
            outside.write_bytes(standard_midi())

            with self.assertRaises(BasicPitchMidiArtifactContractError):
                verify_and_hash_basic_pitch_midi(replace(inference, expected_midi_path=outside))

            inference.expected_midi_path.symlink_to(outside)
            with self.assertRaises(BasicPitchMidiArtifactPathError):
                verify_and_hash_basic_pitch_midi(inference)

    def test_invalid_midi_header_track_layout_or_trailing_bytes_are_rejected(self) -> None:
        """A filename/zero model exit alone cannot turn arbitrary bytes into MIDI evidence."""

        one_track_with_two_declared = standard_midi()
        # The two-byte track-count field starts after `MThd`, its four-byte
        # length, and the two-byte format field: offset 10.  Leave one physical
        # track in place so this is a true framing mismatch, not valid 2-track
        # MIDI generated by the helper.
        one_track_with_two_declared = (
            one_track_with_two_declared[:10]
            + (2).to_bytes(2, "big")
            + one_track_with_two_declared[12:]
        )
        invalid_contents = (
            b"not-a-midi-file",
            one_track_with_two_declared,
            standard_midi(trailing=b"unexpected"),
        )
        for contents in invalid_contents:
            with self.subTest(contents=contents[:4]), tempfile.TemporaryDirectory() as temporary_directory:
                inference = self._inference(Path(temporary_directory))
                inference.expected_midi_path.write_bytes(contents)
                with self.assertRaises(BasicPitchMidiArtifactFormatError):
                    verify_and_hash_basic_pitch_midi(inference)

    def test_widened_command_is_rejected_before_the_output_file_is_read(self) -> None:
        """Artifact verification repeats the process adapter's fixed-command constraint."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            inference.expected_midi_path.write_bytes(standard_midi())
            widened = replace(inference, command=("/bin/sh", "-c", "unexpected"))

            with self.assertRaises(BasicPitchMidiArtifactContractError):
                verify_and_hash_basic_pitch_midi(widened)


if __name__ == "__main__":
    unittest.main()
