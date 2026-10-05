"""Unit tests for the bounded no-shell ADTOF CPU process runner.

Tests create temporary private scratch paths and inject a fake runner. They do
not start subprocesses, import ADTOF/PyTorch, access MinIO/PostgreSQL/RabbitMQ,
or use Docker/Kubernetes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.processing.adtof_cpu_process import (
    ADTOF_CPU_PROCESS_WORKING_DIRECTORY,
    DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS,
    ADTOFCPUProcessContractError,
    ADTOFCPUProcessPathError,
    ADTOFCPUProcessTimedOut,
    ADTOFCPUInferenceCommand,
    run_adtof_cpu_inference_process,
)
from app.processing.adtof_inference_command import build_adtof_cpu_inference_command
from app.artifacts.stem_download import DownloadedADTOFStem
from app.db.stem_task_start import RunningADTOFStem
from app.db.task_claim import ADTOFTaskLease


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

    def test_accepts_the_private_setgid_output_created_under_a_kubernetes_fsgroup(self) -> None:
        """Allow only the harmless setgid bit inherited from an ``emptyDir``.

        Kubernetes prepares this worker's scratch ``emptyDir`` with the Pod
        ``fsGroup`` and its setgid bit. A child output directory therefore has
        mode ``02700`` even though its access permissions are still exactly
        owner-only ``0700``. The process boundary must accept that normal
        Kubernetes filesystem behavior without allowing group or other access.
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            # Simulate the inherited bit directly so this portable unit test
            # does not depend on host filesystem support for group inheritance.
            inference.output_directory.chmod(
                stat.S_IMODE(inference.output_directory.stat().st_mode) | stat.S_ISGID
            )
            runner = FakeRunner()

            returned = run_adtof_cpu_inference_process(inference, runner=runner)

            self.assertEqual(returned, inference)
            self.assertEqual(runner.calls, [(inference, DEFAULT_ADTOF_CPU_PROCESS_TIMEOUT_SECONDS)])

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

    @patch("app.processing.adtof_cpu_process.subprocess.Popen")
    def test_real_runner_uses_the_fixed_application_directory_for_module_imports(self, popen) -> None:
        """The private output directory must not replace `/app` on ``sys.path``.

        The approved command starts ``python -m app.processing.adtof_cpu_inference_entrypoint``.
        Python resolves that package from its CWD, so the subprocess must retain
        the image's fixed application directory rather than executing from the
        disposable output folder. Input and output files remain absolute command
        arguments and do not depend on the CWD.
        """

        with tempfile.TemporaryDirectory() as temporary_directory:
            inference = self._inference(Path(temporary_directory))
            completed_process = MagicMock()
            completed_process.returncode = 0
            popen.return_value = completed_process

            returned = run_adtof_cpu_inference_process(inference)

            self.assertEqual(returned, inference)
            self.assertEqual(popen.call_args.kwargs["cwd"], ADTOF_CPU_PROCESS_WORKING_DIRECTORY)
            self.assertFalse(popen.call_args.kwargs["shell"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
