"""Unit tests for running one ADTOF local CPU task through both artifact checks.

Tests create temporary scratch files and inject a fake process runner. They do
not run ADTOF/PyTorch, use subprocesses, contact MinIO/PostgreSQL/RabbitMQ, or
interact with Docker/Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from app.adtof_cpu_process import ADTOFCPUProcessFailed
from app.adtof_inference_command import ADTOFCPUInferenceCommand
from app.local_task_execution import execute_running_adtof_local_task
from app.output_artifact import ADTOFOutputArtifactFormatError
from app.stem_download import DownloadedADTOFStem
from app.stem_task_start import RunningADTOFStem
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def standard_midi() -> bytes:
    """Return a minimal complete MIDI container for fake ADTOF output."""

    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big") + (120).to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


def tempo_payload() -> dict[str, object]:
    """Return an exact high-confidence cloud-compatible tempo observation."""

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


def running_stem(source_path: Path) -> RunningADTOFStem:
    """Return a committed-running handoff around one private temporary WAV."""

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


class FakeProcessRunner:
    """Write controlled process-shaped results instead of launching ADTOF."""

    def __init__(self, *, midi: bytes = standard_midi(), tempo: bytes | None = None, error: BaseException | None = None) -> None:
        self.midi = midi
        self.tempo = tempo if tempo is not None else json.dumps(tempo_payload()).encode("utf-8")
        self.error = error
        self.calls: list[tuple[ADTOFCPUInferenceCommand, int]] = []

    def run(self, *, inference: ADTOFCPUInferenceCommand, timeout_seconds: int) -> None:
        self.calls.append((inference, timeout_seconds))
        if self.error is not None:
            raise self.error
        inference.midi_output_path.write_bytes(self.midi)
        inference.tempo_output_path.write_bytes(self.tempo)


class ADTOFLocalTaskExecutionTests(unittest.TestCase):
    """Prove local evidence escapes only after one CPU exit and both validations."""

    def _running_stem(self, work_directory: Path) -> RunningADTOFStem:
        """Create the expected temporary directory layout without storage I/O."""

        stem_directory = work_directory / "adtof-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"the earlier boundary already verified this WAV")
        return running_stem(source_path)

    def test_returns_complete_midi_and_tempo_evidence_only_after_process_success(self) -> None:
        """The returned values remain local and preserve the running-task provenance."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            running = self._running_stem(work_directory)
            runner = FakeProcessRunner()

            result = execute_running_adtof_local_task(
                running=running,
                work_directory=work_directory,
                process_timeout_seconds=60,
                process_runner=runner,  # type: ignore[arg-type]
            )

            self.assertEqual(result.running, running)
            self.assertEqual(result.midi.output_plan, result.output_plans.midi)
            self.assertEqual(result.tempo.output_plan, result.output_plans.tempo_candidate)
            self.assertIsNone(result.midi.tempo_candidate)
            self.assertEqual(result.tempo_candidate.bpm, 120.0)
            self.assertTrue(result.tempo_candidate.credible)
            self.assertEqual(runner.calls, [(result.inference, 60)])

    def test_failed_process_or_one_invalid_artifact_never_returns_partial_evidence(self) -> None:
        """No caller gets a MIDI success when tempo/process validation is incomplete."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(ADTOFCPUProcessFailed):
                execute_running_adtof_local_task(
                    running=self._running_stem(work_directory),
                    work_directory=work_directory,
                    process_runner=FakeProcessRunner(
                        error=ADTOFCPUProcessFailed("ADTOF CPU process failed."),
                    ),  # type: ignore[arg-type]
                )

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            with self.assertRaises(ADTOFOutputArtifactFormatError) as captured:
                execute_running_adtof_local_task(
                    running=self._running_stem(work_directory),
                    work_directory=work_directory,
                    process_runner=FakeProcessRunner(tempo=b"not-json"),  # type: ignore[arg-type]
                )
            self.assertIn("artifact", str(captured.exception).lower())


if __name__ == "__main__":
    unittest.main()
