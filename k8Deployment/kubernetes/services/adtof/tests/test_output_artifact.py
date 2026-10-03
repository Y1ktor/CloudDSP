"""Unit tests for ADTOF local MIDI/tempo artifact verification.

The tests write small deterministic temporary files, but never invoke ADTOF or
contact MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.artifacts.output_artifact import (
    ADTOFOutputArtifactContractError,
    ADTOFOutputArtifactFormatError,
    ADTOFOutputArtifactPathError,
    verify_and_hash_adtof_output_artifact,
)
from app.artifacts.output_object_plan import build_adtof_output_object_plans
from app.artifacts.stem_download import DownloadedADTOFStem
from app.db.stem_task_start import RunningADTOFStem
from app.db.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def standard_midi(*, trailing: bytes = b"") -> bytes:
    """Return one minimal complete type-1 Standard MIDI File for validator tests."""

    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big") + (120).to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track + trailing
    )


def running_stem(root: Path) -> RunningADTOFStem:
    """Return a valid running handoff solely to construct its deterministic plans."""

    input_path = root / "stem.wav"
    input_path.write_bytes(b"verified input is owned by the earlier boundary")
    return RunningADTOFStem(
        lease=ADTOFTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            stem_name="drums",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
        ),
        stem=DownloadedADTOFStem(
            stem_path=input_path,
            size_bytes=input_path.stat().st_size,
            sha256="a" * 64,
        ),
        started_at=datetime(2026, 9, 13, 12, 20, tzinfo=UTC),
    )


def tempo_payload() -> dict[str, object]:
    """Return the exact JSON shape produced by the preserved cloud worker."""

    return {
        "extractor": "adtof",
        "bpm": 120.0,
        "beat_count": 16,
        "duration_seconds": 32.5,
        "interval_consistency": 0.9,
        "drum_event_count": 24,
        "credible": True,
        "confidence": "high",
        "source": "adtof_drums",
    }


class ADTOFOutputArtifactTests(unittest.TestCase):
    """Prove exact planned files become local evidence before a future upload."""

    def test_validates_and_hashes_the_fixed_midi_and_tempo_files(self) -> None:
        """Both artifact kinds preserve their own format and output-plan binding."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plans = build_adtof_output_object_plans(running=running_stem(root))
            midi_path = root / "drums.mid"
            midi_bytes = standard_midi()
            midi_path.write_bytes(midi_bytes)
            tempo_path = root / "drums_bpm.json"
            tempo_bytes = json.dumps(tempo_payload(), separators=(",", ":")).encode("utf-8")
            tempo_path.write_bytes(tempo_bytes)

            midi = verify_and_hash_adtof_output_artifact(
                output_plan=plans.midi,
                artifact_path=midi_path,
            )
            tempo = verify_and_hash_adtof_output_artifact(
                output_plan=plans.tempo_candidate,
                artifact_path=tempo_path,
            )

            self.assertEqual(midi.size_bytes, len(midi_bytes))
            self.assertEqual(midi.sha256, hashlib.sha256(midi_bytes).hexdigest())
            self.assertIsNone(midi.tempo_candidate)
            self.assertEqual(tempo.size_bytes, len(tempo_bytes))
            self.assertEqual(tempo.sha256, hashlib.sha256(tempo_bytes).hexdigest())
            self.assertEqual(tempo.tempo_candidate.bpm if tempo.tempo_candidate else None, 120.0)
            self.assertTrue(tempo.tempo_candidate.credible if tempo.tempo_candidate else False)

    def test_mismatched_plan_is_rejected_before_it_can_read_a_local_path(self) -> None:
        """A plan with a widened MinIO key is not a capability to read arbitrary files."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = build_adtof_output_object_plans(running=running_stem(root)).midi
            with self.assertRaises(ADTOFOutputArtifactContractError):
                verify_and_hash_adtof_output_artifact(
                    output_plan=replace(plan, object_key=f"midi/{JOB_ID}/other.mid"),
                    artifact_path=root / "does-not-matter.mid",
                )

    def test_bad_midi_and_tempo_shapes_are_not_artifact_evidence(self) -> None:
        """Names/extensions alone never make arbitrary bytes safe to upload."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plans = build_adtof_output_object_plans(running=running_stem(root))
            midi_path = root / "drums.mid"
            tempo_path = root / "drums_bpm.json"
            invalid_cases = (
                (plans.midi, midi_path, standard_midi(trailing=b"unexpected")),
                (plans.tempo_candidate, tempo_path, b'{"extractor":"adtof","bpm":NaN}'),
                (
                    plans.tempo_candidate,
                    tempo_path,
                    json.dumps({**tempo_payload(), "confidence": "low"}).encode("utf-8"),
                ),
                (
                    plans.tempo_candidate,
                    tempo_path,
                    json.dumps({**tempo_payload(), "drum_event_count": 0}).encode("utf-8"),
                ),
            )
            for plan, path, contents in invalid_cases:
                with self.subTest(plan=plan.artifact_kind, contents=contents[:16]):
                    path.write_bytes(contents)
                    with self.assertRaises(ADTOFOutputArtifactFormatError):
                        verify_and_hash_adtof_output_artifact(output_plan=plan, artifact_path=path)

    def test_symlink_and_wrong_filename_cannot_be_substituted_for_expected_output(self) -> None:
        """The final local component is always a regular planned filename."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan = build_adtof_output_object_plans(running=running_stem(root)).midi
            outside = root / "outside.mid"
            outside.write_bytes(standard_midi())
            linked = root / "drums.mid"
            linked.symlink_to(outside)
            with self.assertRaises(ADTOFOutputArtifactPathError):
                verify_and_hash_adtof_output_artifact(output_plan=plan, artifact_path=linked)
            with self.assertRaises(ADTOFOutputArtifactPathError):
                verify_and_hash_adtof_output_artifact(output_plan=plan, artifact_path=root / "other.mid")


if __name__ == "__main__":
    unittest.main()
