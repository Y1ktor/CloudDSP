"""Bounded shell-free execution of one approved local-CPU Demucs command.

``demucs_command.py`` owns model selection and local scratch-path validation.
This module revalidates that immutable request immediately before it starts a
child process, then gives that child no stdin, no retained stdout/stderr, a
private process group, and a finite deadline. A zero exit status means only
that Demucs completed: the next artifact-verification boundary must still
inspect the output files before they can be uploaded to MinIO.

This module does not download source bytes, call FFprobe, acquire/renew a
PostgreSQL lease, receive RabbitMQ deliveries, publish artifacts, alter task
state, or create Kubernetes resources. A later orchestration layer will keep
lease renewal active while this bounded model process runs. Its separate
renewal-aware runner entrypoint already owns cancellation-safe process polling,
and the execution workspace supplies the PostgreSQL checkpoint for a particular
running task.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from typing import Protocol

from app.demucs_command import DemucsSeparationCommand, build_demucs_separation_command


# The task lease is fifteen minutes. Twelve minutes leaves an explicit margin
# for an outer runtime to renew the lease and handle source/output cleanup,
# while still bounding a damaged local CPU process. This is not a performance
# claim: a later workload test may tune it only alongside lease-renewal policy.
DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS = 12 * 60
MIN_DEMUCS_PROCESS_TIMEOUT_SECONDS = 1
MAX_DEMUCS_PROCESS_TIMEOUT_SECONDS = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS

# The durable lease lasts fifteen minutes, while a normal CPU separation may
# run for twelve. Checking every minute keeps ownership fresh with ample room
# for one short PostgreSQL renewal and graceful child cleanup. A later runtime
# may choose a more frequent value, but it must never silently renew less often
# than this reviewed ceiling.
DEFAULT_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS = 60
MIN_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS = 1
MAX_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS = DEFAULT_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS


class DemucsProcessError(RuntimeError):
    """Base safe operational category for local Demucs process outcomes."""


class DemucsProcessContractError(DemucsProcessError):
    """A caller tried to execute a hand-built or widened command request."""


class DemucsProcessUnavailable(DemucsProcessError):
    """The reviewed image did not provide the fixed Demucs child launcher."""


class DemucsProcessTimedOut(DemucsProcessError):
    """Demucs exceeded the bounded local CPU execution budget."""


class DemucsProcessFailed(DemucsProcessError):
    """Demucs exited unsuccessfully without exposing private child diagnostics."""


class DemucsLeaseRenewalOwnershipLost(RuntimeError):
    """A renewal checkpoint withdrew permission to let the child keep running.

    This is deliberately not a ``DemucsProcessError``: a lost PostgreSQL lease
    is not a model failure and must later become ordinary ownership loss, not a
    user-visible retry/terminal category. The renewal-aware runner has already
    stopped the entire child process group before it raises this safe signal.
    """


class DemucsProcessRunner(Protocol):
    """Injectable process surface that never receives an arbitrary shell string."""

    def run(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
    ) -> None:
        """Run one approved separation and raise a safe category on failure."""


class DemucsLeaseRenewalAwareProcessRunner(Protocol):
    """A Demucs runner that can stop its child at periodic ownership checks.

    A plain synchronous ``DemucsProcessRunner`` cannot be wrapped in a thread
    safely: Python cannot reliably cancel that thread, and it could keep
    reading/writing scratch after PostgreSQL withdrew the lease. This stricter
    protocol requires the runner itself to own the child process group so it
    can terminate it before returning a lost-ownership or checkpoint-error
    signal to a future task runtime.
    """

    def run_with_lease_renewal(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
        renewal_interval_seconds: int,
        renewal_checkpoint: Callable[[], bool],
    ) -> None:
        """Run one request and stop the child when a renewal check says no."""


def _validated_timeout_seconds(value: object) -> int:
    """Require a bounded whole-second model deadline before child creation."""

    if (
        type(value) is not int
        or not MIN_DEMUCS_PROCESS_TIMEOUT_SECONDS <= value <= MAX_DEMUCS_PROCESS_TIMEOUT_SECONDS
    ):
        raise DemucsProcessContractError("Demucs process timeout is invalid.")
    return value


def _validated_renewal_interval_seconds(value: object) -> int:
    """Require a bounded checkpoint cadence before a child process starts."""

    if (
        type(value) is not int
        or not MIN_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS
        <= value
        <= MAX_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS
    ):
        raise DemucsProcessContractError("Demucs lease-renewal interval is invalid.")
    return value


def _approved_separation_or_raise(value: object) -> DemucsSeparationCommand:
    """Rebuild and compare a request so direct dataclass construction cannot widen it.

    Dataclasses are intentionally easy to use in tests, so their type alone is
    not proof that this command came from the reviewed builder. Rebuilding also
    repeats the just-before-exec check that source/output paths remain private,
    non-symlinked, and fresh after an earlier validation step.
    """

    if not isinstance(value, DemucsSeparationCommand):
        raise DemucsProcessContractError("Demucs process command is invalid.")
    try:
        rebuilt = build_demucs_separation_command(
            stem_mode=value.stem_mode,
            source_path=value.source_path,
            output_directory=value.output_directory,
            work_directory=value.work_directory,
        )
    except Exception as error:
        # The command builder's path/contract category is useful at its own
        # boundary. At execution time expose one process category so a later
        # retry/result mapper does not depend on local filesystem detail.
        raise DemucsProcessContractError("Demucs process command is invalid.") from error
    if rebuilt != value:
        raise DemucsProcessContractError("Demucs process command is invalid.")
    return rebuilt


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of Demucs and helpers in its private session."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # Kubernetes will eventually terminate a pathological Pod. Preserve the
        # original safe timeout category instead of raising process diagnostics.
        return


def _wait_for_process_with_lease_renewal(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
    renewal_interval_seconds: int,
    renewal_checkpoint: Callable[[], bool],
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait in bounded slices, stopping the process before a lost lease escapes.

    The callback performs no process action itself. It returns ``True`` only
    after a short durable renewal committed; ``False`` means PostgreSQL no
    longer recognizes this task/token. An exception is also unsafe to ignore:
    the runner stops the full private process group before propagating it, so
    no child can keep writing scratch while the caller decides what to do.

    Monotonic time is used solely for local waiting. PostgreSQL remains the
    authority on lease validity and calculates its own expiry, so no Pod wall
    clock participates in an ownership decision.
    """

    started_at = monotonic_clock()
    deadline = started_at + timeout_seconds
    next_renewal_at = started_at + renewal_interval_seconds

    while process.poll() is None:
        now = monotonic_clock()
        if now >= deadline:
            _stop_process_group(process)
            raise DemucsProcessTimedOut("Demucs process timed out.")

        # ``wait`` wakes at the nearest hard deadline or renewal checkpoint.
        # A tiny positive bound avoids a busy loop if a mock/platform timer
        # returns early; a real process may still finish before that timeout.
        wait_seconds = min(deadline - now, next_renewal_at - now)
        if wait_seconds <= 0:
            wait_seconds = 0.001
        try:
            process.wait(timeout=wait_seconds)
            return
        except subprocess.TimeoutExpired:
            # The child could finish between `wait` timing out and this poll.
            # Treat that as success rather than running an unnecessary renewal.
            if process.poll() is not None:
                return

        now = monotonic_clock()
        if now >= deadline:
            _stop_process_group(process)
            raise DemucsProcessTimedOut("Demucs process timed out.")
        if now < next_renewal_at:
            # A spurious/early timeout must not increase database traffic or
            # invent an elapsed renewal interval; wait again for the same tick.
            continue

        try:
            keep_running = renewal_checkpoint()
        except Exception:
            _stop_process_group(process)
            raise
        if type(keep_running) is not bool:
            _stop_process_group(process)
            raise TypeError("Demucs lease-renewal checkpoint must return a boolean.")
        if not keep_running:
            _stop_process_group(process)
            raise DemucsLeaseRenewalOwnershipLost("Demucs task lease ownership was lost.")

        # Schedule from the completed check rather than catching up through a
        # burst of late renewals after a slow database. The 15-minute lease and
        # <=60-second cadence still provide a wide durable safety margin.
        next_renewal_at = now + renewal_interval_seconds


class SubprocessDemucsProcessRunner:
    """Production runner for a fixed Demucs command in Pod-local scratch."""

    def run(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
    ) -> None:
        """Run without shell/stdin/log capture and stop the whole group on timeout."""

        self._run_approved(
            separation=separation,
            timeout_seconds=timeout_seconds,
        )

    def run_with_lease_renewal(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
        renewal_interval_seconds: int,
        renewal_checkpoint: Callable[[], bool],
    ) -> None:
        """Run one fixed child while periodically proving PostgreSQL ownership."""

        self._run_approved(
            separation=separation,
            timeout_seconds=timeout_seconds,
            renewal_interval_seconds=renewal_interval_seconds,
            renewal_checkpoint=renewal_checkpoint,
        )

    def _run_approved(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
        renewal_interval_seconds: int | None = None,
        renewal_checkpoint: Callable[[], bool] | None = None,
    ) -> None:
        """Start and finish one approved child, optionally at renewal checkpoints."""

        approved = _approved_separation_or_raise(separation)
        bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
        if (renewal_interval_seconds is None) != (renewal_checkpoint is None):
            raise DemucsProcessContractError("Demucs lease-renewal process setup is invalid.")
        bounded_renewal_interval_seconds: int | None = None
        if renewal_interval_seconds is not None:
            bounded_renewal_interval_seconds = _validated_renewal_interval_seconds(renewal_interval_seconds)
            if not callable(renewal_checkpoint):
                raise DemucsProcessContractError("Demucs lease-renewal checkpoint is invalid.")
        try:
            process = subprocess.Popen(
                approved.command,
                cwd=str(approved.output_directory),
                stdin=subprocess.DEVNULL,
                # Demucs progress and child diagnostics can contain local paths.
                # Do not retain either stream in memory or expose it through a
                # durable job status; later observability uses reviewed metrics.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise DemucsProcessUnavailable("Demucs executable is unavailable.") from error
        except OSError as error:
            raise DemucsProcessError("Demucs process could not start.") from error

        if renewal_checkpoint is None:
            try:
                process.wait(timeout=bounded_timeout_seconds)
            except subprocess.TimeoutExpired as error:
                _stop_process_group(process)
                raise DemucsProcessTimedOut("Demucs process timed out.") from error
        else:
            # The two optional values were validated together above. The local
            # assertions narrow them for type checkers without changing the
            # runtime guard that protects production callers.
            assert bounded_renewal_interval_seconds is not None
            _wait_for_process_with_lease_renewal(
                process,
                timeout_seconds=bounded_timeout_seconds,
                renewal_interval_seconds=bounded_renewal_interval_seconds,
                renewal_checkpoint=renewal_checkpoint,
            )

        if process.returncode != 0:
            raise DemucsProcessFailed("Demucs process failed.")


def run_demucs_separation(
    separation: DemucsSeparationCommand,
    *,
    timeout_seconds: int = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    runner: DemucsProcessRunner | None = None,
) -> DemucsSeparationCommand:
    """Run only an approved separation and return it after a zero process exit.

    The returned request lets a later artifact verifier locate the expected
    worker-owned output tree. It does *not* assert that any stem exists or that
    it is valid; zero exit is merely the process boundary's limited success
    condition. Tests inject a runner so no unit test needs Demucs/Torch or a
    real audio source.
    """

    approved = _approved_separation_or_raise(separation)
    bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
    selected_runner = runner if runner is not None else SubprocessDemucsProcessRunner()
    selected_runner.run(separation=approved, timeout_seconds=bounded_timeout_seconds)
    return approved


def run_demucs_separation_with_lease_renewal(
    separation: DemucsSeparationCommand,
    *,
    renewal_checkpoint: Callable[[], bool],
    timeout_seconds: int = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS,
    renewal_interval_seconds: int = DEFAULT_DEMUCS_LEASE_RENEWAL_INTERVAL_SECONDS,
    runner: DemucsLeaseRenewalAwareProcessRunner | None = None,
) -> DemucsSeparationCommand:
    """Run one approved child with cancellation-safe periodic lease checks.

    A plain runner is rejected rather than being run in a background thread:
    if PostgreSQL withdraws the lease, the worker must stop the *actual* child
    process before returning ownership loss. The production subprocess runner
    owns that child group; source tests may inject a runner that explicitly
    implements the same cancellation-safe protocol.

    This function knows only the boolean checkpoint contract. A later runtime
    converts the durable ``running_lease_renewal`` result to that boolean and
    handles ownership loss after the child has fully stopped. It opens no
    database transaction and makes no RabbitMQ/MinIO/Kubernetes call itself.
    """

    approved = _approved_separation_or_raise(separation)
    bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
    bounded_renewal_interval_seconds = _validated_renewal_interval_seconds(renewal_interval_seconds)
    if not callable(renewal_checkpoint):
        raise DemucsProcessContractError("Demucs lease-renewal checkpoint is invalid.")
    selected_runner = runner if runner is not None else SubprocessDemucsProcessRunner()
    renewal_run = getattr(selected_runner, "run_with_lease_renewal", None)
    if not callable(renewal_run):
        raise DemucsProcessContractError("Demucs process runner cannot safely renew a lease.")
    renewal_run(
        separation=approved,
        timeout_seconds=bounded_timeout_seconds,
        renewal_interval_seconds=bounded_renewal_interval_seconds,
        renewal_checkpoint=renewal_checkpoint,
    )
    return approved
