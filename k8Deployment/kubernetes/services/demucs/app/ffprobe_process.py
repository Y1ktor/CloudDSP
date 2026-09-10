"""Bounded subprocess adapter for the Demucs FFprobe admission check.

The future worker first downloads one already-authorized MinIO object into its
own Pod-local work directory.  This adapter then runs a fixed FFprobe command
against that local regular file and passes only its bounded standard output to
the strict :mod:`app.audio_probe` parser.

No shell is used, stderr is never retained in application memory/logs, and the
child is placed in its own process group so a timeout or output-limit failure
also stops children it may have created.  This module does not download data,
make a storage/database/broker request, renew a lease, or start Demucs.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Protocol

from app.audio_probe import (
    MAX_FFPROBE_OUTPUT_BYTES,
    VerifiedDemucsAudioProbe,
    parse_verified_demucs_audio_probe,
)


# This command intentionally contains no input-derived option.  The path comes
# only after ``-i`` and is first proven to be a non-symlink regular file below,
# so a filename beginning with '-' cannot become an FFprobe option.  JSON is
# parsed by ``audio_probe.py`` rather than by ad-hoc text splitting.
FFPROBE_EXECUTABLE = "ffprobe"
FFPROBE_ARGUMENTS = (
    "-v",
    "error",
    "-show_format",
    "-show_streams",
    "-of",
    "json",
)

# The source length has already been capped at 500 seconds and 256 MiB before
# this layer.  Thirty seconds is therefore a generous metadata-probe ceiling,
# while still preventing a damaged container from occupying a worker forever.
DEMUCS_FFPROBE_TIMEOUT_SECONDS = 30.0

# Read in modest chunks.  We stop before retaining more than the parser's
# one-MiB JSON allowance, instead of using ``communicate()`` and discovering an
# oversized result only after it was allocated in the worker process.
FFPROBE_STDOUT_READ_CHUNK_BYTES = 64 * 1024


class DemucsFFprobeExecutionError(RuntimeError):
    """Safe operational error from the isolated FFprobe process boundary."""


class DemucsFFprobeSourcePathError(DemucsFFprobeExecutionError):
    """The caller gave a path outside the worker-owned local work directory."""


class DemucsFFprobeUnavailable(DemucsFFprobeExecutionError):
    """The reviewed worker image did not make FFprobe executable available."""


class DemucsFFprobeTimedOut(DemucsFFprobeExecutionError):
    """FFprobe exceeded the bounded source-admission time budget."""


class DemucsFFprobeOutputLimitExceeded(DemucsFFprobeExecutionError):
    """FFprobe emitted more metadata than this worker may retain or parse."""


class DemucsFFprobeProcessFailed(DemucsFFprobeExecutionError):
    """FFprobe exited unsuccessfully without exposing raw diagnostic output."""


class DemucsFFprobeRunner(Protocol):
    """Narrow injectable process surface used by the adapter and its unit tests."""

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        timeout_seconds: float,
        max_stdout_bytes: int,
    ) -> bytes:
        """Run one command and return only bounded successful stdout bytes."""


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out/oversized command and any child processes it spawned.

    ``start_new_session=True`` below makes the FFprobe process the leader of a
    private process group.  Killing that group prevents a rare helper process
    from surviving after this Pod-local admission attempt has failed.  Cleanup
    is deliberately best effort: the original bounded error remains useful if
    the process already exited between polling and signalling.
    """

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
        # A future caller will let Kubernetes terminate a pathological worker
        # Pod. Do not replace the original error with a process-management one.
        return


def _bounded_stdout(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
    max_stdout_bytes: int,
) -> bytes:
    """Read stdout incrementally, enforcing both the time and byte limits."""

    stdout = process.stdout
    if stdout is None:  # Defensive: the runner itself always requests PIPE.
        raise DemucsFFprobeExecutionError("Demucs FFprobe output channel is unavailable.")

    output = bytearray()
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise DemucsFFprobeTimedOut("Demucs FFprobe timed out.")
        try:
            ready, _, _ = select.select((stdout,), (), (), remaining_seconds)
        except (OSError, ValueError) as error:
            raise DemucsFFprobeExecutionError("Demucs FFprobe output read failed.") from error
        if not ready:
            raise DemucsFFprobeTimedOut("Demucs FFprobe timed out.")

        # Read at most one byte beyond the retained allowance. That lets the
        # adapter detect an oversized result without ever storing its full body.
        next_read_size = min(
            FFPROBE_STDOUT_READ_CHUNK_BYTES,
            max_stdout_bytes - len(output) + 1,
        )
        try:
            chunk = os.read(stdout.fileno(), next_read_size)
        except OSError as error:
            raise DemucsFFprobeExecutionError("Demucs FFprobe output read failed.") from error
        if not chunk:
            return bytes(output)
        if len(output) + len(chunk) > max_stdout_bytes:
            raise DemucsFFprobeOutputLimitExceeded("Demucs FFprobe output exceeds its limit.")
        output.extend(chunk)


class SubprocessDemucsFFprobeRunner:
    """Production implementation of the bounded, shell-free FFprobe runner."""

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        timeout_seconds: float,
        max_stdout_bytes: int,
    ) -> bytes:
        """Run one fixed command, suppress stderr, and return bounded stdout only."""

        if not command or timeout_seconds <= 0 or max_stdout_bytes < 1:
            raise DemucsFFprobeExecutionError("Demucs FFprobe runner configuration is invalid.")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                # FFprobe diagnostics can mention paths/container metadata. The
                # safe public exceptions below intentionally do not retain them.
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise DemucsFFprobeUnavailable("Demucs FFprobe executable is unavailable.") from error
        except OSError as error:
            raise DemucsFFprobeExecutionError("Demucs FFprobe could not start.") from error

        try:
            deadline = time.monotonic() + timeout_seconds
            output = _bounded_stdout(
                process,
                deadline=deadline,
                max_stdout_bytes=max_stdout_bytes,
            )
            # The stdout read above owns the real deadline. ``wait`` normally
            # returns immediately after EOF, but keep a short bounded guard in
            # case an unusual child closes stdout before it exits.
            process.wait(timeout=max(deadline - time.monotonic(), 0.001))
        except (DemucsFFprobeExecutionError, subprocess.TimeoutExpired) as error:
            _stop_process_group(process)
            if isinstance(error, subprocess.TimeoutExpired):
                raise DemucsFFprobeTimedOut("Demucs FFprobe timed out.") from error
            raise
        finally:
            # ``Popen`` owns this Python file object even though ``os.read``
            # consumed the descriptor directly above. Close it on every path
            # so timeout/overflow tests and long-running worker loops never
            # accumulate one file descriptor per failed media probe.
            if process.stdout is not None:
                process.stdout.close()

        if process.returncode != 0:
            raise DemucsFFprobeProcessFailed("Demucs FFprobe could not read the source.")
        return output


def _worker_owned_regular_source_path(*, source_path: object, work_directory: object) -> Path:
    """Resolve one existing non-symlink regular file beneath the worker's scratch.

    This is not an authorization check: PostgreSQL and MinIO established that
    earlier. It is a local-process boundary that keeps FFprobe from receiving a
    host path, a substituted symlink, a directory, or a FIFO. The later MinIO
    download adapter will create this file in a bounded ``emptyDir`` volume and
    remove the whole work directory when its Pod exits.
    """

    if not isinstance(source_path, Path) or not isinstance(work_directory, Path):
        raise DemucsFFprobeSourcePathError("Demucs FFprobe source path is invalid.")
    try:
        if work_directory.is_symlink() or source_path.is_symlink():
            raise DemucsFFprobeSourcePathError("Demucs FFprobe source path is invalid.")
        resolved_work_directory = work_directory.resolve(strict=True)
        resolved_source_path = source_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DemucsFFprobeSourcePathError("Demucs FFprobe source path is invalid.") from error
    if not resolved_work_directory.is_dir() or not resolved_source_path.is_file():
        raise DemucsFFprobeSourcePathError("Demucs FFprobe source path is invalid.")
    try:
        resolved_source_path.relative_to(resolved_work_directory)
    except ValueError as error:
        raise DemucsFFprobeSourcePathError("Demucs FFprobe source path is invalid.") from error
    return resolved_source_path


def run_verified_demucs_audio_probe(
    *,
    source_path: Path,
    work_directory: Path,
    runner: DemucsFFprobeRunner | None = None,
) -> VerifiedDemucsAudioProbe:
    """Run the fixed FFprobe admission command, then validate its JSON result.

    The caller supplies a Path it has already downloaded into a per-lease,
    bounded Pod-local work directory. A default production runner executes
    FFprobe without a shell, gives it no stdin, bounds stdout while it is read,
    discards stderr, and terminates its process group on timeout/overflow.
    Tests can inject the small runner protocol without needing FFprobe.

    This adapter returns the parser's exact validated evidence, or a safe
    operational/process exception. It does not decide a PostgreSQL task result
    or RabbitMQ acknowledgement; a later worker composition will map each
    category while holding the current task lease token.
    """

    validated_source_path = _worker_owned_regular_source_path(
        source_path=source_path,
        work_directory=work_directory,
    )
    command = (
        FFPROBE_EXECUTABLE,
        *FFPROBE_ARGUMENTS,
        "-i",
        str(validated_source_path),
    )
    selected_runner = runner if runner is not None else SubprocessDemucsFFprobeRunner()
    output = selected_runner.run(
        command=command,
        cwd=validated_source_path.parent,
        timeout_seconds=DEMUCS_FFPROBE_TIMEOUT_SECONDS,
        max_stdout_bytes=MAX_FFPROBE_OUTPUT_BYTES,
    )
    return parse_verified_demucs_audio_probe(output)
