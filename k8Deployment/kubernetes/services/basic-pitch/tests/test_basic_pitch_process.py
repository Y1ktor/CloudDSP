"""Unit tests for the fixed, bounded Basic Pitch CLI process adapter.

Tests create only temporary files/directories and inject a fake runner. They
never execute Basic Pitch, contact MinIO/PostgreSQL/RabbitMQ, or use Docker or
Kubernetes.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.processing.basic_pitch_process import (
    BASIC_PITCH_EXECUTABLE,
    BASIC_PITCH_OUTPUT_DIRECTORY_NAME,
    DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS,
    BasicPitchProcessContractError,
    BasicPitchProcessPathError,
    BasicPitchProcessTimedOut,
    BasicPitchInferenceCommand,
    build_basic_pitch_inference_command,
    run_basic_pitch_inference,
)
from app.artifacts.stem_download import DownloadedBasicPitchStem
from app.db.stem_task_start import RunningBasicPitchStem
from app.db.task_lease import BasicPitchTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


class FakeRunner:
    """Record the one approved request without starting a model process."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[BasicPitchInferenceCommand, int]] = []

    def run(self, *, inference: BasicPitchInferenceCommand, timeout_seconds: int) -> None:
        self.calls.append((inference, timeout_seconds))
        if self.error is not None:
            raise self.error


def running_stem(source_path: Path) -> RunningBasicPitchStem:
    """Return current-lease evidence paired with one existing private stem path."""

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


class BasicPitchProcessAdapterTests(unittest.TestCase):
    """Prove only a fresh worker-owned stem can form/execute the fixed CLI command."""

    def _approved_running_stem(self, work_directory: Path) -> RunningBasicPitchStem:
        """Create the exact scratch shape produced by the download boundary."""

        stem_directory = work_directory / "basic-pitch-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"placeholder; fake runner never reads audio")
        return running_stem(source_path)

    def test_builds_exact_cli_and_fresh_private_output_location(self) -> None:
        """No caller-provided CLI option, model selection, or output path is accepted."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory) / "scratch"
            work_directory.mkdir()
            running = self._approved_running_stem(work_directory)

            command = build_basic_pitch_inference_command(
                running=running,
                work_directory=work_directory,
            )

            expected_output = running.stem.stem_path.parent / BASIC_PITCH_OUTPUT_DIRECTORY_NAME
            self.assertEqual(command.output_directory, expected_output.resolve())
            self.assertEqual(command.expected_midi_path, expected_output.resolve() / "stem_basic_pitch.mid")
            self.assertEqual(
                command.command,
                (BASIC_PITCH_EXECUTABLE, str(expected_output.resolve()), str(running.stem.stem_path.resolve())),
            )
            self.assertEqual(command.output_directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(list(command.output_directory.iterdir()), [])

    def test_unsafe_stem_or_existing_output_directory_never_forms_a_command(self) -> None:
        """Host paths, symlinks, renamed files, and stale output are all rejected."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "scratch"
            work_directory.mkdir()
            running = self._approved_running_stem(work_directory)
            outside = root / "outside.wav"
            outside.write_bytes(b"outside")
            symlink_path = running.stem.stem_path.parent / "linked.wav"
            symlink_path.symlink_to(outside)
            wrong_name = running.stem.stem_path.parent / "browser-name.wav"
            wrong_name.write_bytes(b"wrong generic name")

            for path in (outside, symlink_path, wrong_name):
                with self.subTest(path=path.name):
                    with self.assertRaises(BasicPitchProcessPathError):
                        build_basic_pitch_inference_command(
                            running=running_stem(path),
                            work_directory=work_directory,
                        )

            output_directory = running.stem.stem_path.parent / BASIC_PITCH_OUTPUT_DIRECTORY_NAME
            output_directory.mkdir()
            with self.assertRaises(BasicPitchProcessPathError):
                build_basic_pitch_inference_command(running=running, work_directory=work_directory)

    def test_only_approved_empty_command_runs_with_the_bounded_timeout(self) -> None:
        """A fake runner proves command revalidation occurs before any model process."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            inference = build_basic_pitch_inference_command(
                running=self._approved_running_stem(work_directory),
                work_directory=work_directory,
            )
            runner = FakeRunner()

            returned = run_basic_pitch_inference(inference, runner=runner)

            self.assertEqual(returned, inference)
            self.assertEqual(runner.calls, [(inference, DEFAULT_BASIC_PITCH_PROCESS_TIMEOUT_SECONDS)])

    def test_tampered_command_failing_runner_and_invalid_timeout_do_not_claim_success(self) -> None:
        """Process failure policy remains outside this adapter, with no false output proof."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            inference = build_basic_pitch_inference_command(
                running=self._approved_running_stem(work_directory),
                work_directory=work_directory,
            )
            tampered = BasicPitchInferenceCommand(
                command=("/bin/sh", "-c", "unexpected"),
                source_path=inference.source_path,
                output_directory=inference.output_directory,
                expected_midi_path=inference.expected_midi_path,
                work_directory=inference.work_directory,
            )
            runner = FakeRunner()

            with self.assertRaises(BasicPitchProcessContractError):
                run_basic_pitch_inference(tampered, runner=runner)
            with self.assertRaises(BasicPitchProcessContractError):
                run_basic_pitch_inference(inference, timeout_seconds=0, runner=runner)
            self.assertEqual(runner.calls, [])

            timeout_runner = FakeRunner(BasicPitchProcessTimedOut("Basic Pitch process timed out."))
            with self.assertRaises(BasicPitchProcessTimedOut):
                run_basic_pitch_inference(inference, timeout_seconds=60, runner=timeout_runner)
            self.assertEqual(timeout_runner.calls, [(inference, 60)])
