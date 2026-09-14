"""Unit tests for the bounded no-shell ADTOF CPU process runner.

Tests create temporary private scratch paths and inject a fake runner. They do
not start subprocesses, import ADTOF/PyTorch, access MinIO/PostgreSQL/RabbitMQ,
or use Docker/Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from app.adtof_cpu_process import (
    DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    ADTOFCPUProcessContractError,
    ADTOFCPUProcessPathError,
    ADTOFCPUProcessTimedOut,
    ADTOFCPUInferenceCommand,
    run_adtof_cpu_inference_process,
)
from app.adtof_inference_command import build_adtof_cpu_inference_command
from app.stem_download import DownloadedADTOFStem
from app.stem_task_start import RunningADTOFStem
from app.task_claim import ADTOFTaskLease


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


class FakeRunner:
    """Record approved requests without starting a real model process."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple[ADTOFCPUInferenceCommand, int]] = []

    def run(self, *, inference: ADTOFCPUInferenceCommand, timeout_seconds: int) -> None:
        self.calls.append((inference, timeout_seconds))
        if self.error is not None:
            raise self.error


def running_stem(source_path: Path) -> RunningADTOFStem:
    """Return committed ADTOF drums evidence around an existing temporary WAV."""

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


class ADTOFCPUProcessRunnerTests(unittest.TestCase):
    """Prove only a fresh fixed private request reaches an injectable runner."""

    def _inference(self, work_directory: Path) -> ADTOFCPUInferenceCommand:
        """Build the exact command/output layout reserved by the prior boundary."""

        stem_directory = work_directory / "adtof-stem-example"
        stem_directory.mkdir()
        source_path = stem_directory / "stem.wav"
        source_path.write_bytes(b"fake process runner never reads this audio")
        return build_adtof_cpu_inference_command(
            running=running_stem(source_path),
            work_directory=work_directory,
        )

    def test_runs_only_the_approved_empty_command_under_the_default_deadline(self) -> None:
        """The fake proves command revalidation happens before any model launch."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            runner = FakeRunner()

            returned = run_adtof_cpu_inference_process(inference, runner=runner)

            self.assertEqual(returned, inference)
            self.assertEqual(runner.calls, [(inference, DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS)])

    def test_tampered_command_or_stale_output_never_reaches_the_runner(self) -> None:
        """A frozen dataclass cannot add shell args or reuse a prior model result."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            runner = FakeRunner()
            tampered = replace(inference, command=("/bin/sh", "-c", "unexpected"))

            with self.assertRaises(ADTOFCPUProcessContractError):
                run_adtof_cpu_inference_process(tampered, runner=runner)
            inference.midi_output_path.write_bytes(b"stale")
            with self.assertRaises(ADTOFCPUProcessPathError):
                run_adtof_cpu_inference_process(inference, runner=runner)
            self.assertEqual(runner.calls, [])

    def test_invalid_timeout_or_runner_timeout_never_fabricates_success(self) -> None:
        """Timeout policy propagates a safe category and leaves no false evidence."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            runner = FakeRunner()
            with self.assertRaises(ADTOFCPUProcessContractError):
                run_adtof_cpu_inference_process(inference, timeout_seconds=0, runner=runner)
            self.assertEqual(runner.calls, [])

            timeout_runner = FakeRunner(ADTOFCPUProcessTimedOut("ADTOF CPU process timed out."))
            with self.assertRaises(ADTOFCPUProcessTimedOut):
                run_adtof_cpu_inference_process(inference, timeout_seconds=60, runner=timeout_runner)
            self.assertEqual(timeout_runner.calls, [(inference, 60)])


if __name__ == "__main__":
    unittest.main()
