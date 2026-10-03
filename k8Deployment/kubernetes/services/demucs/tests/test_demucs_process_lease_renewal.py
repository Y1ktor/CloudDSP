"""Tests for cancellation-safe periodic lease checks around one Demucs child.

All runner/process objects are fakes. These tests do not start Demucs/Torch,
open PostgreSQL/MinIO/RabbitMQ, wait in real time, build an image, or change a
Kubernetes resource. They prove only the checked child-process control flow.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.processing.demucs_command import DemucsSeparationCommand, build_demucs_separation_command
from app.processing.demucs_process import (
    DEFAULT_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS,
    DemucsLeaseRenewalOwnershipLost,
    DemucsProcessContractError,
    _wait_for_process_with_lease_renewal,
    run_demucs_separation_with_lease_renewal,
)


class RenewalAwareFakeRunner:
    """Record one renewal-aware call without creating an operating-system child."""

    def __init__(self) -> None:
        """Start with no calls so each test can prove one exact invocation."""

        self.calls: list[tuple[DemucsSeparationCommand, int, int, object]] = []

    def run_with_lease_renewal(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
        renewal_interval_seconds: int,
        renewal_checkpoint: object,
    ) -> None:
        """Store the complete immutable boundary request without executing it."""

        self.calls.append(
            (
                separation,
                timeout_seconds,
                renewal_interval_seconds,
                renewal_checkpoint,
            )
        )


class PlainFakeRunner:
    """Model an old synchronous runner that cannot cancel its child safely."""

    def run(self, **_kwargs: object) -> None:
        """This method must never be selected by the renewal-aware function."""

        raise AssertionError("A plain runner cannot satisfy renewal cancellation.")


class TimedOutFakeProcess:
    """Simulate one pollable child that reaches exactly one renewal checkpoint."""

    def __init__(self, polls: list[int | None]) -> None:
        """Use a scripted poll sequence and record bounded waits."""

        self._polls = list(polls)
        self.wait_timeouts: list[float] = []

    def poll(self) -> int | None:
        """Return the next scripted child state without a real process."""

        if not self._polls:
            raise AssertionError("process was polled more often than expected")
        return self._polls.pop(0)

    def wait(self, *, timeout: float) -> int:
        """Timeout once so the monitor must evaluate its renewal callback."""

        self.wait_timeouts.append(timeout)
        raise subprocess.TimeoutExpired(cmd=("demucs",), timeout=timeout)


def approved_separation() -> tuple[tempfile.TemporaryDirectory[str], DemucsSeparationCommand]:
    """Create one valid private-scratch separation request for public seam tests."""

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


class DemucsLeaseRenewalProcessTests(unittest.TestCase):
    """Prove periodic checks require a runner that owns cancellable children."""

    def test_public_boundary_forwards_exact_approved_request_to_safe_runner(self) -> None:
        """The callback and bounded cadence stay paired with one approved command."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = RenewalAwareFakeRunner()
        checkpoint = lambda: True

        returned = run_demucs_separation_with_lease_renewal(
            separation,
            renewal_checkpoint=checkpoint,
            timeout_seconds=120,
            renewal_interval_seconds=30,
            runner=runner,  # type: ignore[arg-type]
        )

        self.assertEqual(returned, separation)
        self.assertEqual(runner.calls, [(separation, 120, 30, checkpoint)])

    def test_plain_synchronous_runner_is_rejected_before_it_can_start_work(self) -> None:
        """A background thread cannot substitute for process-group cancellation."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)

        with self.assertRaisesRegex(DemucsProcessContractError, "cannot safely renew a lease"):
            run_demucs_separation_with_lease_renewal(
                separation,
                renewal_checkpoint=lambda: True,
                runner=PlainFakeRunner(),  # type: ignore[arg-type]
            )

    def test_default_interval_is_the_reviewed_one_minute_ceiling(self) -> None:
        """An omitted configuration cannot silently become an infrequent renewal."""

        temporary_directory, separation = approved_separation()
        self.addCleanup(temporary_directory.cleanup)
        runner = RenewalAwareFakeRunner()

        run_demucs_separation_with_lease_renewal(
            separation,
            renewal_checkpoint=lambda: True,
            runner=runner,  # type: ignore[arg-type]
        )

        self.assertEqual(runner.calls[0][2], DEFAULT_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS)

    def test_completed_checkpoint_allows_the_same_child_wait_loop_to_continue(self) -> None:
        """A committed renewal schedules the next interval without busy looping."""

        process = TimedOutFakeProcess([None, None, 0])
        clock_values = iter((0.0, 0.0, 60.0))
        checkpoint_calls: list[str] = []

        _wait_for_process_with_lease_renewal(
            process,  # type: ignore[arg-type]
            timeout_seconds=120,
            renewal_interval_seconds=60,
            renewal_checkpoint=lambda: checkpoint_calls.append("renewed") is None,
            monotonic_clock=lambda: next(clock_values),
        )

        self.assertEqual(process.wait_timeouts, [60.0])
        self.assertEqual(checkpoint_calls, ["renewed"])

    def test_lost_ownership_stops_the_child_before_exposing_the_stop_signal(self) -> None:
        """No stale model child may survive after PostgreSQL withdraws its token."""

        process = TimedOutFakeProcess([None, None])
        clock_values = iter((0.0, 0.0, 60.0))

        with patch("app.processing.demucs_process._stop_process_group") as stop:
            with self.assertRaises(DemucsLeaseRenewalOwnershipLost):
                _wait_for_process_with_lease_renewal(
                    process,  # type: ignore[arg-type]
                    timeout_seconds=120,
                    renewal_interval_seconds=60,
                    renewal_checkpoint=lambda: False,
                    monotonic_clock=lambda: next(clock_values),
                )

        stop.assert_called_once_with(process)

    def test_checkpoint_exception_stops_the_child_before_propagating_it(self) -> None:
        """An uncertain renewal cannot leave a still-running stale process behind."""

        process = TimedOutFakeProcess([None, None])
        clock_values = iter((0.0, 0.0, 60.0))
        failure = RuntimeError("private renewal database failure")

        with patch("app.processing.demucs_process._stop_process_group") as stop:
            with self.assertRaises(RuntimeError) as raised:
                _wait_for_process_with_lease_renewal(
                    process,  # type: ignore[arg-type]
                    timeout_seconds=120,
                    renewal_interval_seconds=60,
                    renewal_checkpoint=lambda: (_ for _ in ()).throw(failure),
                    monotonic_clock=lambda: next(clock_values),
                )

        self.assertIs(raised.exception, failure)
        stop.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
