"""Unit tests for the bounded Demucs process-execution adapter.

All tests inject a recorder/failing fake runner. They never invoke the Demucs
binary, load Torch, create output stems, contact any cluster service, or start
Docker/Kubernetes work.
"""

from __future__ import annotations

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from app.demucs_command import DemucsSeparationCommand, build_demucs_separation_command
from app.demucs_process import (
    DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    DemucsProcessContractError,
    DemucsProcessTimedOut,
    run_demucs_separation,
)


class FakeRunner:
    """Record an approved request without spawning any process."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[DemucsSeparationCommand, int]] = []

    def run(self, *, separation: DemucsSeparationCommand, timeout_seconds: int) -> None:
        """Store the immutable command request or reproduce one safe failure."""

        self.calls.append((separation, timeout_seconds))
        if self.failure is not None:
            raise self.failure


def approved_separation() -> tuple[tempfile.TemporaryDirectory[str], DemucsSeparationCommand]:
    """Create one temporary worker-scratch request that the real builder accepts."""

    temporary_directory = tempfile.TemporaryDirectory()
    work_directory = Path(temporary_directory.name) / "scratch"
    work_directory.mkdir()
    source_path = work_directory / "input" / "source.media"
    source_path.parent.mkdir()
    source_path.write_bytes(b"earlier-boundaries-validated-these-bytes")
    output_directory = work_directory / "output"
    output_directory.mkdir()
    return temporary_directory, build_demucs_separation_command(
        stem_mode="4-stems",
        source_path=source_path,
        output_directory=output_directory,
        work_directory=work_directory,
    )


class DemucsProcessAdapterTests(unittest.TestCase):
    """Prove only a current builder request can reach an injectable runner."""

    def test_approved_command_reaches_runner_with_default_bounded_deadline(self) -> None:
        """The adapter returns only after its runner reports a zero-exit success."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = FakeRunner()

        returned = run_demucs_separation(separation, runner=runner)

        self.assertEqual(returned, separation)
        self.assertEqual(runner.calls, [(separation, DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS)])

    def test_tampered_dataclass_is_rejected_before_any_runner_call(self) -> None:
        """A later caller cannot add options or switch the fixed executable."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = FakeRunner()
        tampered = replace(separation, command=("/bin/sh", "-c", "unexpected"))

        with self.assertRaises(DemucsProcessContractError):
            run_demucs_separation(tampered, runner=runner)

        self.assertEqual(runner.calls, [])

    def test_runner_safe_failure_propagates_without_a_result_or_retry_policy(self) -> None:
        """The later result adapter, not this boundary, decides retry or failure."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = FakeRunner(DemucsProcessTimedOut("Demucs process timed out."))

        with self.assertRaises(DemucsProcessTimedOut):
            run_demucs_separation(separation, timeout_seconds=60, runner=runner)

        self.assertEqual(runner.calls, [(separation, 60)])

    def test_invalid_timeout_is_rejected_before_any_runner_call(self) -> None:
        """A deployment typo cannot make a Demucs child run indefinitely."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = FakeRunner()

        with self.assertRaises(DemucsProcessContractError):
            run_demucs_separation(separation, timeout_seconds=0, runner=runner)

        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
