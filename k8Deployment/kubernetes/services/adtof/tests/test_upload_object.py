"""Unit tests for complete ADTOF local-artifact-to-MinIO upload plans.

Tests produce controlled temporary local artifacts through a fake process runner.
They do not invoke ADTOF, contact MinIO/PostgreSQL/RabbitMQ, or use Docker or
Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from app.processing.adtof_inference_command import ADTOFCPUInferenceCommand
from app.processing.local_task_execution import execute_running_adtof_local_task
from app.artifacts.stem_download import DownloadedADTOFStem
from app.db.stem_task_start import RunningADTOFStem
from app.db.task_claim import ADTOFTaskLease
from app.artifacts.upload_object import ADTOFUploadObjectPlanContractError, build_adtof_upload_objects


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def standard_midi() -> bytes:
    """Return a small complete MIDI file for one controlled local result."""

    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big") + (120).to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


def tempo_payload() -> dict[str, object]:
    """Return a strict cloud-compatible tempo JSON object."""

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


class FakeRunner:
    """Write controlled artifacts at exactly the command's reserved paths."""

    def run(self, *, inference: ADTOFCPUInferenceCommand, timeout_seconds: int) -> None:
        """Model the successful child process without a subprocess or model package."""

        del timeout_seconds
        inference.midi_output_path.write_bytes(standard_midi())
        inference.tempo_output_path.write_bytes(json.dumps(tempo_payload()).encode("utf-8"))


def running_stem(source_path: Path) -> RunningADTOFStem:
    """Return a current drums lease around one temporary private WAV path."""

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
            stem_path=source_path,
            size_bytes=source_path.stat().st_size,
            sha256="a" * 64,
        ),
        started_at=datetime(2026, 9, 13, 12, 20, tzinfo=UTC),
    )


class ADTOFUploadObjectTests(unittest.TestCase):
    """Prove current dual local evidence produces complete immutable upload plans."""

    def _local_outputs(self, work_directory: Path):
        """Build one full local result through prior bounded, fake-runner stages."""

        stem_directory = work_directory / "adtof-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"input belongs to the earlier verified boundary")
        return execute_running_adtof_local_task(
            running=running_stem(source_path),
            work_directory=work_directory,
            process_runner=FakeRunner(),  # type: ignore[arg-type]
        )

    def test_builds_two_complete_private_upload_plans_with_integrity_metadata(self) -> None:
        """No later S3 call needs to infer bytes, MIME type, provenance, or keys."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = self._local_outputs(Path(temporary_directory))

            plans = build_adtof_upload_objects(local_outputs=outputs)

            self.assertEqual(plans.midi.bucket, "clouddsp-uploads")
            self.assertEqual(plans.midi.object_key, f"midi/{JOB_ID}/drums.mid")
            self.assertEqual(plans.midi.content_type, "audio/midi")
            self.assertEqual(plans.tempo_candidate.object_key, f"midi/{JOB_ID}/drums_bpm.json")
            self.assertEqual(plans.tempo_candidate.content_type, "application/json")
            self.assertEqual(
                plans.midi.s3_metadata[-2:],
                (("size-bytes", str(outputs.midi.size_bytes)), ("sha256", outputs.midi.sha256)),
            )
            self.assertEqual(
                plans.tempo_candidate.s3_metadata[-2:],
                (("size-bytes", str(outputs.tempo.size_bytes)), ("sha256", outputs.tempo.sha256)),
            )
            self.assertEqual(plans.midi.s3_metadata[:-2], outputs.output_plans.midi.base_s3_metadata)
            self.assertEqual(
                plans.tempo_candidate.s3_metadata[:-2],
                outputs.output_plans.tempo_candidate.base_s3_metadata,
            )

    def test_changed_local_bytes_cannot_reuse_prior_verified_evidence(self) -> None:
        """The upload plan repeats validation instead of trusting a cached hash."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = self._local_outputs(Path(temporary_directory))
            outputs.midi.path.write_bytes(b"not a midi file")

            with self.assertRaises(ADTOFUploadObjectPlanContractError):
                build_adtof_upload_objects(local_outputs=outputs)


if __name__ == "__main__":
    unittest.main()
