"""Unit tests for one Basic Pitch MIDI local-evidence-to-object-plan contract.

The tests create a tiny temporary Standard MIDI File and run no model or
network client. They do not contact MinIO, PostgreSQL, RabbitMQ, Docker, or
Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.basic_pitch_process import BasicPitchInferenceCommand, build_basic_pitch_inference_command
from app.basic_pitch_requested_message import BasicPitchRequestedMessage
from app.midi_artifact import VerifiedBasicPitchMidiArtifact, verify_and_hash_basic_pitch_midi
from app.midi_output_object import (
    BasicPitchMidiOutputObjectContractError,
    build_basic_pitch_midi_output_object,
)
from app.stem_download import DownloadedBasicPitchStem
from app.stem_task_start import RunningBasicPitchStem
from app.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"
INPUT_SHA256 = "a" * 64


def standard_midi() -> bytes:
    """Return one minimal, structurally valid type-1 Standard MIDI File."""

    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big") + (120).to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


def message() -> BasicPitchRequestedMessage:
    """Return one strict-delivery-shaped value for the vocals Basic Pitch task."""

    return BasicPitchRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="vocals",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/vocals.wav",
        stem_content_length=101,
        stem_sha256=INPUT_SHA256,
    )


def lease() -> BasicPitchTaskLease:
    """Return matching durable task identity with the real Basic Pitch input shape."""

    return BasicPitchTaskLease(
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


def artifact(root: Path) -> VerifiedBasicPitchMidiArtifact:
    """Build then locally verify one process-shaped Basic Pitch MIDI output."""

    work_directory = root / "scratch"
    work_directory.mkdir(parents=True)
    stem_directory = work_directory / "basic-pitch-stem-example"
    stem_directory.mkdir()
    source_path = stem_directory / "stem.wav"
    source_path.write_bytes(b"input already verified in the earlier boundary")
    running = RunningBasicPitchStem(
        lease=lease(),
        stem=DownloadedBasicPitchStem(
            stem_path=source_path,
            size_bytes=source_path.stat().st_size,
            sha256=INPUT_SHA256,
        ),
        started_at=datetime(2026, 9, 11, 12, 20, tzinfo=UTC),
    )
    inference: BasicPitchInferenceCommand = build_basic_pitch_inference_command(
        running=running,
        work_directory=work_directory,
    )
    inference.expected_midi_path.write_bytes(standard_midi())
    return verify_and_hash_basic_pitch_midi(inference)


class BasicPitchMidiOutputObjectTests(unittest.TestCase):
    """Prove only joined current durable/local evidence can name a private MIDI object."""

    def test_builds_fixed_private_key_and_complete_immutable_metadata(self) -> None:
        """Recovery uses the stable Job/stem coordinate, never an attempt-specific key."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            verified = artifact(Path(temporary_directory))

            output = build_basic_pitch_midi_output_object(
                lease=lease(),
                message=message(),
                artifact=verified,
            )

            self.assertEqual(output.bucket, "clouddsp-uploads")
            self.assertEqual(output.object_key, f"midi/{JOB_ID}/vocals.mid")
            self.assertEqual(output.local_path, verified.path)
            self.assertEqual(output.content_type, "audio/midi")
            self.assertEqual(output.content_length, verified.size_bytes)
            self.assertEqual(
                output.s3_metadata,
                (
                    ("schema-version", "1"),
                    ("producer", "basic-pitch"),
                    ("job-id", JOB_ID),
                    ("task-id", TASK_ID),
                    ("request-event-id", EVENT_ID),
                    ("stem-name", "vocals"),
                    ("stem-mode", "4-stems"),
                    ("size-bytes", str(verified.size_bytes)),
                    ("sha256", verified.sha256),
                    ("input-stem-sha256", INPUT_SHA256),
                ),
            )

    def test_mismatched_lease_or_forged_cached_artifact_is_rejected(self) -> None:
        """Frozen data cannot broaden a task's output key or replace current bytes."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            verified = artifact(Path(temporary_directory))
            with self.assertRaises(BasicPitchMidiOutputObjectContractError):
                build_basic_pitch_midi_output_object(
                    lease=replace(lease(), stem_name="bass"),
                    message=message(),
                    artifact=verified,
                )
            with self.assertRaises(BasicPitchMidiOutputObjectContractError):
                build_basic_pitch_midi_output_object(
                    lease=lease(),
                    message=message(),
                    artifact=replace(verified, sha256="0" * 64),
                )

    def test_same_size_file_change_after_verification_cannot_reuse_old_evidence(self) -> None:
        """The contract repeats local framing/hash proof before it forms an upload plan."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            verified = artifact(Path(temporary_directory))
            verified.path.write_bytes(b"x" * verified.size_bytes)

            with self.assertRaises(BasicPitchMidiOutputObjectContractError):
                build_basic_pitch_midi_output_object(
                    lease=lease(),
                    message=message(),
                    artifact=verified,
                )


if __name__ == "__main__":
    unittest.main()
