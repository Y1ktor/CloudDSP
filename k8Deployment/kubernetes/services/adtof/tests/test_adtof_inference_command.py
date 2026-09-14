"""Unit tests for ADTOF's fixed CPU command/output-path construction.

Tests create temporary scratch files/directories only. They never import or
invoke ADTOF/PyTorch, start a subprocess, access MinIO/PostgreSQL/RabbitMQ, or
use Docker or Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from app.adtof_inference_command import (
    ADTOF_CPU_ENTRYPOINT_MODULE,
    ADTOF_PYTHON_EXECUTABLE,
    ADTOFInferenceCommandContractError,
    ADTOFInferenceCommandPathError,
    ADTOF_OUTPUT_DIRECTORY_NAME,
    build_adtof_cpu_inference_command,
)
from app.model_configuration import (
    ADTOF_CPU_DEVICE,
    ADTOF_FPS,
    ADTOF_MODEL_CONFIGURATION_ID,
    ADTOF_MODEL_SOURCE_REVISION,
    ADTOF_THRESHOLDS_ARGUMENT,
)
from app.stem_download import DownloadedADTOFStem
from app.stem_task_start import RunningADTOFStem
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def running_stem(source_path: Path) -> RunningADTOFStem:
    """Return current ADTOF evidence around one existing private stem path."""

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


class ADTOFInferenceCommandTests(unittest.TestCase):
    """Prove only a fresh private drums input forms the fixed CPU model argv."""

    def _approved_running_stem(self, work_directory: Path) -> RunningADTOFStem:
        """Create the exact generic scratch shape from the stream-download boundary."""

        stem_directory = work_directory / "adtof-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"placeholder; this task never runs a model")
        return running_stem(source_path)

    def test_reserves_fresh_outputs_and_builds_the_exact_cpu_no_shell_command(self) -> None:
        """No caller can select a model, device, CLI flag, or output filename."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory) / "scratch"
            work_directory.mkdir()
            running = self._approved_running_stem(work_directory)

            inference = build_adtof_cpu_inference_command(
                running=running,
                work_directory=work_directory,
            )

            expected_output_directory = running.stem.stem_path.parent / ADTOF_OUTPUT_DIRECTORY_NAME
            self.assertEqual(inference.output_directory, expected_output_directory.resolve())
            self.assertEqual(inference.midi_output_path, expected_output_directory.resolve() / "drums.mid")
            self.assertEqual(inference.tempo_output_path, expected_output_directory.resolve() / "drums_bpm.json")
            self.assertEqual(inference.output_directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(list(inference.output_directory.iterdir()), [])
            self.assertEqual(
                inference.command,
                (
                    ADTOF_PYTHON_EXECUTABLE,
                    "-m",
                    ADTOF_CPU_ENTRYPOINT_MODULE,
                    "--input-wav",
                    str(running.stem.stem_path.resolve()),
                    "--midi-output",
                    str(expected_output_directory.resolve() / "drums.mid"),
                    "--tempo-output",
                    str(expected_output_directory.resolve() / "drums_bpm.json"),
                    "--model-revision",
                    ADTOF_MODEL_SOURCE_REVISION,
                    "--fps",
                    str(ADTOF_FPS),
                    "--thresholds",
                    ADTOF_THRESHOLDS_ARGUMENT,
                    "--device",
                    ADTOF_CPU_DEVICE,
                ),
            )
            self.assertEqual(inference.model_configuration_id, ADTOF_MODEL_CONFIGURATION_ID)

    def test_unsafe_stem_or_existing_output_directory_never_forms_a_command(self) -> None:
        """Host paths, links, wrong names, and stale output are all rejected."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "scratch"
            work_directory.mkdir()
            valid = self._approved_running_stem(work_directory)
            outside = root / "outside.wav"
            outside.write_bytes(b"outside")
            linked = valid.stem.stem_path.parent / "linked.wav"
            linked.symlink_to(outside)
            wrong_name = valid.stem.stem_path.parent / "browser-name.wav"
            wrong_name.write_bytes(b"wrong generic name")

            for source_path in (outside, linked, wrong_name):
                with self.subTest(source_path=source_path.name):
                    with self.assertRaises(ADTOFInferenceCommandPathError):
                        build_adtof_cpu_inference_command(
                            running=running_stem(source_path),
                            work_directory=work_directory,
                        )

            (valid.stem.stem_path.parent / ADTOF_OUTPUT_DIRECTORY_NAME).mkdir()
            with self.assertRaises(ADTOFInferenceCommandPathError):
                build_adtof_cpu_inference_command(running=valid, work_directory=work_directory)

    def test_forged_running_identity_is_rejected_before_a_path_is_opened(self) -> None:
        """The command builder reuses the output planner's complete lease proof."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            valid = self._approved_running_stem(work_directory)
            forged = replace(valid, lease=replace(valid.lease, stem_name="vocals"))

            with self.assertRaises(ADTOFInferenceCommandContractError):
                build_adtof_cpu_inference_command(running=forged, work_directory=work_directory)
            self.assertFalse((valid.stem.stem_path.parent / ADTOF_OUTPUT_DIRECTORY_NAME).exists())


if __name__ == "__main__":
    unittest.main()
