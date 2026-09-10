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
lease renewal active while this bounded model process runs.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Protocol

from app.demucs_command import DemucsSeparationCommand, build_demucs_separation_command


# The task lease is fifteen minutes. Twelve minutes leaves an explicit margin
# for an outer runtime to renew the lease and handle source/output cleanup,
# while still bounding a damaged local CPU process. This is not a performance
# claim: a later workload test may tune it only alongside lease-renewal policy.
DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS = 12 * 60
MIN_DEMUCS_PROCESS_TIMEOUT_SECONDS = 1
MAX_DEMUCS_PROCESS_TIMEOUT_SECONDS = DEFAULT_DEMUCS_PROCESS_TIMEOUT_SECONDS


class DemucsProcessError(RuntimeError):
    """Base safe operational category for local Demucs process outcomes."""


class DemucsProcessContractError(DemucsProcessError):
    """A caller tried to execute a hand-built or widened command request."""


class DemucsProcessUnavailable(DemucsProcessError):
    """The reviewed image did not provide the fixed Demucs executable."""


class DemucsProcessTimedOut(DemucsProcessError):
    """Demucs exceeded the bounded local CPU execution budget."""


class DemucsProcessFailed(DemucsProcessError):
    """Demucs exited unsuccessfully without exposing private child diagnostics."""


class DemucsProcessRunner(Protocol):
    """Injectable process surface that never receives an arbitrary shell string."""

    def run(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
    ) -> None:
        """Run one approved separation and raise a safe category on failure."""


def _validated_timeout_seconds(value: object) -> int:
    """Require a bounded whole-second model deadline before child creation."""

    if (
        type(value) is not int
        or not MIN_DEMUCS_PROCESS_TIMEOUT_SECONDS <= value <= MAX_DEMUCS_PROCESS_TIMEOUT_SECONDS
    ):
        raise DemucsProcessContractError("Demucs process timeout is invalid.")
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


class SubprocessDemucsProcessRunner:
    """Production runner for a fixed Demucs command in Pod-local scratch."""

    def run(
        self,
        *,
        separation: DemucsSeparationCommand,
        timeout_seconds: int,
    ) -> None:
        """Run without shell/stdin/log capture and stop the whole group on timeout."""

        approved = _approved_separation_or_raise(separation)
        bounded_timeout_seconds = _validated_timeout_seconds(timeout_seconds)
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

        try:
            process.wait(timeout=bounded_timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _stop_process_group(process)
            raise DemucsProcessTimedOut("Demucs process timed out.") from error

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
