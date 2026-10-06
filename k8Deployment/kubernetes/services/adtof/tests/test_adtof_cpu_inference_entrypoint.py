"""Unit tests for the constrained ADTOF CPU child-process entrypoint.

The tests create temporary scratch files and inject model/audio stand-ins. They
never import ADTOF/PyTorch/Librosa/PrettyMIDI, start a subprocess, contact
MinIO/PostgreSQL/RabbitMQ, or use Docker/Kubernetes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from app.processing.adtof_cpu_inference_entrypoint import (
    ADTOFCPUInferenceEntrypointContractError,
    ADTOFCPUInferenceEntrypointModelError,
    ADTOFCPUInferenceEntrypointPathError,
    parse_adtof_cpu_inference_argv,
    run_adtof_cpu_inference_entrypoint,
)
from app.processing.adtof_inference_command import build_adtof_cpu_inference_command
from app.processing.model_configuration import ADTOF_CPU_DEVICE, ADTOF_FPS, ADTOF_THRESHOLDS
from app.artifacts.stem_download import DownloadedADTOFStem
from app.db.stem_task_start import RunningADTOFStem
from app.db.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def standard_midi() -> bytes:
    """Return a minimal valid MIDI file for the injected transcriber's output."""

    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big") + (120).to_bytes(2, "big")
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


def running_stem(source_path: Path) -> RunningADTOFStem:
    """Return a committed-running private stem for command/entrypoint tests."""

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


def command_argv(root: Path) -> tuple[str, ...]:
    """Create the one parent-built argv, excluding Python's module prefix."""

    stem_directory = root / "adtof-stem-example"
    stem_directory.mkdir()
    source_path = stem_directory / "stem.wav"
    source_path.write_bytes(b"valid private input placeholder")
    command = build_adtof_cpu_inference_command(
        running=running_stem(source_path),
        work_directory=root,
    )
    return command.command[3:]


def tempo_candidate(drum_event_count: int) -> dict[str, object]:
    """Return a cloud-compatible high-confidence candidate for fake execution."""

    return {
        "bpm": 120.0,
        "beat_count": 16,
        "duration_seconds": 32.5,
        "interval_consistency": 0.9,
        "drum_event_count": drum_event_count,
        "credible": True,
        "confidence": "high",
        "source": "adtof_drums",
    }


class ADTOFCPUInferenceEntrypointTests(unittest.TestCase):
    """Prove only exact parent-built argv can produce the two planned local files."""

    def test_runs_injected_cpu_model_and_writes_cloud_compatible_tempo_json(self) -> None:
        """The model receives only fixed CPU arguments and has no service clients."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argv = command_argv(root)
            calls: list[tuple[Path, Path, tuple[float, ...], int, str]] = []

            def transcriber(input_path, midi_path, *, thresholds, fps, device) -> None:
                calls.append((input_path, midi_path, thresholds, fps, device))
                midi_path.write_bytes(standard_midi())

            def estimator(input_path: Path, drum_event_count: int) -> dict[str, object]:
                self.assertTrue(input_path.is_file())
                return tempo_candidate(drum_event_count)

            returned = run_adtof_cpu_inference_entrypoint(
                argv,
                transcriber=transcriber,
                drum_event_counter=lambda _midi_path: 24,
                tempo_estimator=estimator,
            )

            self.assertEqual(returned.midi_output_path.read_bytes(), standard_midi())
            self.assertEqual(json.loads(returned.tempo_output_path.read_text()), {"extractor": "adtof", **tempo_candidate(24)})
            self.assertEqual(
                calls,
                [
                    (
                        returned.input_wav_path,
                        returned.midi_output_path,
                        ADTOF_THRESHOLDS,
                        ADTOF_FPS,
                        ADTOF_CPU_DEVICE,
                    )
                ],
            )

    def test_changed_flag_order_or_configuration_is_rejected_before_model_execution(self) -> None:
        """The entrypoint has no user-selectable model/device/threshold knobs."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argv = command_argv(root)
            altered_cases = (
                (argv[2], argv[3], argv[0], argv[1], *argv[4:]),
                (*argv[:11], "0.1,0.2,0.3,0.4,0.5", *argv[12:]),
            )
            for altered in altered_cases:
                with self.subTest(altered=altered):
                    with self.assertRaises(ADTOFCPUInferenceEntrypointContractError):
                        run_adtof_cpu_inference_entrypoint(
                            altered,
                            transcriber=lambda *_args, **_kwargs: self.fail("model must not run"),
                        )

    def test_stale_output_and_invalid_tempo_evidence_are_safe_failures(self) -> None:
        """A stale directory or malformed estimator result cannot report model success."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argv = command_argv(root)
            request = parse_adtof_cpu_inference_argv(argv)
            request.midi_output_path.write_bytes(b"stale")
            with self.assertRaises(ADTOFCPUInferenceEntrypointPathError):
                run_adtof_cpu_inference_entrypoint(argv, transcriber=lambda *_args, **_kwargs: None)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argv = command_argv(root)

            def transcriber(_input_path, midi_path, **_kwargs) -> None:
                midi_path.write_bytes(standard_midi())

            with self.assertRaises(ADTOFCPUInferenceEntrypointModelError):
                run_adtof_cpu_inference_entrypoint(
                    argv,
                    transcriber=transcriber,
                    drum_event_counter=lambda _midi_path: 24,
                    tempo_estimator=lambda _input_path, _count: {"unexpected": "shape"},
                )


if __name__ == "__main__":
    unittest.main()
