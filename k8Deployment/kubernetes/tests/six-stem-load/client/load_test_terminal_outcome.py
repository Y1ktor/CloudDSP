"""Private handoff for the complete load-test result.

The PostgreSQL observer writes a point-in-time snapshot, but that snapshot is
not an end-to-end pass. A separate orchestration/observer process writes this
terminal report only after it has combined every required evidence source
(PostgreSQL, MinIO, RabbitMQ, and worker/KEDA behavior). The lifecycle broker
waits for this distinct file before it revokes temporary identities. The
authenticated load client must never mount the report volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import stat
import time
from typing import Final, Literal

from lifecycle_handoff import HandoffError, _atomic_private_json_write, _read_private_json


LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME: Final[str] = "six-stem-load-terminal-outcome.json"
_SCHEMA_VERSION: Final[int] = 1
_RUN_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{8,24}$")
_VALID_STATUSES: Final[frozenset[str]] = frozenset({"succeeded", "failed"})
_SAFE_FAILURE_CODE: Final[str] = "load_test_validation_failed"
_POLL_INTERVAL_SECONDS: Final[float] = 0.2


class LoadTestTerminalOutcomeError(RuntimeError):
    """A safe terminal-outcome handoff failure without private run evidence."""


@dataclass(frozen=True)
class LoadTestTerminalOutcome:
    """One explicit end-to-end terminal decision bound to the current run.

    Success contains no free-form details. Failure carries one fixed category,
    not SQL messages, object keys, queue payloads, or service diagnostics.
    """

    run_marker: str
    status: Literal["succeeded", "failed"]
    failure_code: str | None = None


def prepare_empty_load_test_terminal_outcome_directory(directory: Path) -> None:
    """Reject unsafe or stale outcome paths before provisioning temporary users."""

    try:
        metadata = directory.lstat()
    except FileNotFoundError as error:
        raise LoadTestTerminalOutcomeError(
            "load-test observer report directory was absent"
        ) from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise LoadTestTerminalOutcomeError(
            "load-test observer report path was not a real directory"
        )
    if metadata.st_mode & stat.S_IWOTH:
        raise LoadTestTerminalOutcomeError(
            "load-test observer report directory allowed other-user writes"
        )
    for path in (
        directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME,
        directory / f".{LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME}.tmp",
    ):
        if path.exists() or path.is_symlink():
            raise LoadTestTerminalOutcomeError(
                "load-test observer report directory contained stale outcome data"
            )


def write_load_test_terminal_outcome(
    directory: Path, *, outcome: LoadTestTerminalOutcome
) -> Path:
    """Atomically publish one run-bound result after all evidence checks finish."""

    _validate_outcome(outcome)
    destination = directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise LoadTestTerminalOutcomeError(
            "load-test terminal outcome already existed"
        )
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "run_marker": outcome.run_marker,
        "status": outcome.status,
        "failure_code": outcome.failure_code,
    }
    _atomic_private_json_write(destination, payload)
    return destination


def read_load_test_terminal_outcome(
    directory: Path, *, expected_run_marker: str
) -> LoadTestTerminalOutcome:
    """Validate exact fields and reject a terminal record from another run."""

    _validate_run_marker(expected_run_marker)
    try:
        payload = _read_private_json(
            directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME,
            purpose="load-test terminal outcome",
        )
        if set(payload) != {
            "schema_version",
            "run_marker",
            "status",
            "failure_code",
        }:
            raise LoadTestTerminalOutcomeError(
                "load-test terminal outcome schema was invalid"
            )
        version = payload.get("schema_version")
        if type(version) is not int or version != _SCHEMA_VERSION:
            raise LoadTestTerminalOutcomeError(
                "load-test terminal outcome version was invalid"
            )
        if payload.get("run_marker") != expected_run_marker:
            raise LoadTestTerminalOutcomeError(
                "load-test terminal outcome belonged to another run"
            )
        outcome = LoadTestTerminalOutcome(
            run_marker=expected_run_marker,
            status=payload.get("status"),  # type: ignore[arg-type] -- validated below.
            failure_code=payload.get("failure_code"),
        )
        _validate_outcome(outcome)
        return outcome
    except LoadTestTerminalOutcomeError:
        raise
    except (HandoffError, TypeError, ValueError):
        # Hide JSON/file details at this cross-container trust boundary.
        raise LoadTestTerminalOutcomeError(
            "load-test terminal outcome could not be validated"
        ) from None


def wait_for_load_test_terminal_outcome(
    directory: Path, *, expected_run_marker: str, timeout_seconds: float
) -> LoadTestTerminalOutcome:
    """Wait only for the explicit final-result filename under a finite budget.

    In particular, a PostgreSQL snapshot file in the same directory does not
    satisfy this waiter. That deliberate separation prevents a single
    intermediate observation from triggering identity revocation or cleanup.
    """

    _validate_run_marker(expected_run_marker)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    outcome_path = directory / LOAD_TEST_TERMINAL_OUTCOME_FILE_NAME
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if outcome_path.exists() or outcome_path.is_symlink():
            return read_load_test_terminal_outcome(
                directory,
                expected_run_marker=expected_run_marker,
            )
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    raise LoadTestTerminalOutcomeError(
        "load-test observer did not publish a terminal outcome before the deadline"
    )


def _validate_outcome(outcome: LoadTestTerminalOutcome) -> None:
    """Restrict writers and readers to the same two terminal states."""

    if not isinstance(outcome, LoadTestTerminalOutcome):
        raise LoadTestTerminalOutcomeError("load-test terminal outcome was invalid")
    _validate_run_marker(outcome.run_marker)
    if outcome.status == "succeeded":
        if outcome.failure_code is not None:
            raise LoadTestTerminalOutcomeError(
                "successful load-test outcome contained a failure code"
            )
    elif outcome.status == "failed":
        if outcome.failure_code != _SAFE_FAILURE_CODE:
            raise LoadTestTerminalOutcomeError(
                "failed load-test outcome contained an unsafe failure code"
            )
    else:
        raise LoadTestTerminalOutcomeError(
            "load-test terminal outcome status was invalid"
        )


def _validate_run_marker(run_marker: object) -> None:
    """Accept only the opaque marker shape generated by the broker."""

    if not isinstance(run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(run_marker):
        raise LoadTestTerminalOutcomeError("load-test run marker was invalid")
