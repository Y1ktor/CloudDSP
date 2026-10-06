"""Unit tests for the Demucs bounded FFprobe subprocess adapter.

The adapter tests inject a tiny fake runner; they never need FFprobe, MinIO,
PostgreSQL, RabbitMQ, a model, or Kubernetes. Two narrow runner tests use the
current Python interpreter only to prove its generic pipe bound/error handling.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.processing.audio_probe import MAX_FFPROBE_OUTPUT_BYTES, DemucsAudioProbeProtocolError
from app.processing.ffprobe_process import (
    DEMUCS_FFPROBE_TIMEOUT_SECONDS,
    FFPROBE_ARGUMENTS,
    FFPROBE_EXECUTABLE,
    DemucsFFprobeOutputLimitExceeded,
    DemucsFFprobeProcessFailed,
    DemucsFFprobeSourcePathError,
    DemucsFFprobeTimedOut,
    SubprocessDemucsFFprobeRunner,
    run_verified_demucs_audio_probe,
)


class FakeRunner:
    """Record the one command boundary without creating an external process."""

    def __init__(self, output: bytes = b'{"format":{"duration":"12.5"},"streams":[{"codec_type":"audio"}]}') -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        timeout_seconds: float,
        max_stdout_bytes: int,
    ) -> bytes:
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "max_stdout_bytes": max_stdout_bytes,
            }
        )
        return self.output


class DemucsFFprobeProcessAdapterTests(unittest.TestCase):
    """Prove the adapter fixes its command and protects the local path boundary."""

    def test_fixed_command_is_run_only_for_a_worker_owned_regular_file(self) -> None:
        """The injectable runner sees no source-derived options or ambient cwd."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            source_path = work_directory / "-ordinary-source.wav"
            source_path.write_bytes(b"placeholder-only; fake runner does not parse it")
            runner = FakeRunner()

            verified = run_verified_demucs_audio_probe(
                source_path=source_path,
                work_directory=work_directory,
                runner=runner,
            )

            self.assertEqual(verified.duration_seconds, Decimal("12.5"))
            self.assertEqual(verified.audio_stream_count, 1)
            self.assertEqual(len(runner.calls), 1)
            call = runner.calls[0]
            self.assertEqual(
                call["command"],
                (FFPROBE_EXECUTABLE, *FFPROBE_ARGUMENTS, "-i", str(source_path.resolve())),
            )
            self.assertEqual(call["cwd"], work_directory.resolve())
            self.assertEqual(call["timeout_seconds"], DEMUCS_FFPROBE_TIMEOUT_SECONDS)
            self.assertEqual(call["max_stdout_bytes"], MAX_FFPROBE_OUTPUT_BYTES)

    def test_outside_symlink_or_non_regular_source_never_reaches_runner(self) -> None:
        """Local scratch isolation is checked before any child process can start."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory) / "work"
            work_directory.mkdir()
            outside_path = Path(temporary_directory) / "outside.wav"
            outside_path.write_bytes(b"not an input in worker scratch")
            directory_path = work_directory / "directory-as-source"
            directory_path.mkdir()
            symlink_path = work_directory / "linked.wav"
            symlink_path.symlink_to(outside_path)
            runner = FakeRunner()

            for candidate in (outside_path, directory_path, symlink_path):
                with self.subTest(candidate=candidate.name):
                    with self.assertRaises(DemucsFFprobeSourcePathError):
                        run_verified_demucs_audio_probe(
                            source_path=candidate,
                            work_directory=work_directory,
                            runner=runner,
                        )
            self.assertEqual(runner.calls, [])

    def test_parser_errors_remain_visible_after_the_process_boundary(self) -> None:
        """The adapter never reinterprets malformed JSON as a successful source."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory)
            source_path = work_directory / "source.wav"
            source_path.write_bytes(b"placeholder")

            with self.assertRaisesRegex(DemucsAudioProbeProtocolError, "Demucs FFprobe output is invalid"):
                run_verified_demucs_audio_probe(
                    source_path=source_path,
                    work_directory=work_directory,
                    runner=FakeRunner(output=b"not-json"),
                )

    def test_runner_stops_before_retaining_an_oversized_stdout_body(self) -> None:
        """A generic child cannot make the worker buffer unbounded FFprobe output."""

        runner = SubprocessDemucsFFprobeRunner()
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(DemucsFFprobeOutputLimitExceeded):
                runner.run(
                    command=(
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'x' * 33)",
                    ),
                    cwd=Path(temporary_directory),
                    timeout_seconds=1,
                    max_stdout_bytes=32,
                )

    def test_runner_reports_timeout_and_nonzero_exit_without_child_diagnostics(self) -> None:
        """Operational process outcomes use safe categories rather than stderr text."""

        runner = SubprocessDemucsFFprobeRunner()
        with tempfile.TemporaryDirectory() as temporary_directory:
            working_directory = Path(temporary_directory)
            with self.assertRaises(DemucsFFprobeTimedOut):
                runner.run(
                    command=(sys.executable, "-c", "import time; time.sleep(1)"),
                    cwd=working_directory,
                    timeout_seconds=0.05,
                    max_stdout_bytes=32,
                )
            with self.assertRaises(DemucsFFprobeProcessFailed):
                runner.run(
                    command=(sys.executable, "-c", "raise SystemExit(7)"),
                    cwd=working_directory,
                    timeout_seconds=1,
                    max_stdout_bytes=32,
                )


if __name__ == "__main__":
    unittest.main()
