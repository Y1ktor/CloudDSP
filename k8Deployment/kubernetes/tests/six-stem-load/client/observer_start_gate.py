"""Coordinate observer readiness before the broker releases test traffic.

The lifecycle broker writes one marker-only request before it creates a
Keycloak identity. The observer verifies that RabbitMQ is empty and that the
three KEDA-managed workers are idle, then writes a private ready marker. This
prevents pre-existing queue traffic from being mistaken for this test and
ensures KEDA observation begins before the load client can submit a Job.
"""

from __future__ import annotations

from pathlib import Path
import re
import stat
import time
from typing import Final

from lifecycle_handoff import (
    BROKER_FAILURE_FILE_NAME,
    HandoffError,
    _atomic_private_json_write,
    _read_private_json,
    broker_failure_observed,
)


START_FILE: Final[str] = "six-stem-load-observer-start.json"
READY_FILE: Final[str] = "six-stem-load-observer-ready.json"
_MARKER: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{8,24}$")
_POLL_SECONDS: Final[float] = 0.2


class ObserverStartGateError(RuntimeError):
    """A safe observer-readiness error without private run coordinates."""


def prepare_observer_start_gate(directory: Path) -> None:
    """Reject stale gate files before one new load run is created."""

    try:
        mode = directory.lstat()
    except FileNotFoundError as error:
        raise ObserverStartGateError("observer gate directory was absent") from error
    if not stat.S_ISDIR(mode.st_mode) or stat.S_ISLNK(mode.st_mode) or mode.st_mode & stat.S_IWOTH:
        raise ObserverStartGateError("observer gate directory was unsafe")
    for name in (
        START_FILE,
        READY_FILE,
        BROKER_FAILURE_FILE_NAME,
        f".{START_FILE}.tmp",
        f".{READY_FILE}.tmp",
    ):
        path = directory / name
        if path.exists() or path.is_symlink():
            raise ObserverStartGateError("observer gate contained stale state")


def write_observer_start_request(directory: Path, *, run_marker: str) -> None:
    """Publish only the generated run marker; no user or Job data is included."""

    _validate_marker(run_marker)
    _atomic_private_json_write(directory / START_FILE, {
        "schema_version": 1,
        "run_marker": run_marker,
    })


def wait_for_observer_start_request(
    directory: Path, *, timeout_seconds: float
) -> str:
    """Wait for the broker's start request under a finite startup budget."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    path = directory / START_FILE
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        # A broker that fails before publishing START must wake this container
        # immediately; otherwise the Pod stays Running through a ten-minute
        # startup wait even though no test traffic can ever be released.
        if broker_failure_observed(directory):
            raise ObserverStartGateError("lifecycle broker failed before observer start")
        if path.exists() or path.is_symlink():
            try:
                payload = _read_private_json(path, purpose="observer start request")
                if set(payload) != {"schema_version", "run_marker"} or payload.get("schema_version") != 1:
                    raise ObserverStartGateError("observer start request was invalid")
                marker = payload.get("run_marker")
                _validate_marker(marker)
                return marker
            except (HandoffError, TypeError, ValueError):
                raise ObserverStartGateError("observer start request could not be validated") from None
        time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    raise ObserverStartGateError("observer start request was not published before deadline")


def write_observer_ready(directory: Path, *, run_marker: str, ready: bool) -> None:
    """Signal a run-bound preflight result without exposing service diagnostics."""

    _validate_marker(run_marker)
    if type(ready) is not bool:
        raise ObserverStartGateError("observer readiness value was invalid")
    path = directory / READY_FILE
    if path.exists() or path.is_symlink():
        raise ObserverStartGateError("observer readiness was already published")
    _atomic_private_json_write(path, {
        "schema_version": 1,
        "run_marker": run_marker,
        "ready": ready,
    })


def wait_for_observer_ready(
    directory: Path, *, run_marker: str, timeout_seconds: float
) -> bool:
    """Wait until the trusted observer accepts or rejects its baseline."""

    _validate_marker(run_marker)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    path = directory / READY_FILE
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists() or path.is_symlink():
            try:
                payload = _read_private_json(path, purpose="observer readiness")
                if (
                    set(payload) != {"schema_version", "run_marker", "ready"}
                    or payload.get("schema_version") != 1
                    or payload.get("run_marker") != run_marker
                    or type(payload.get("ready")) is not bool
                ):
                    raise ObserverStartGateError("observer readiness report was invalid")
                return payload["ready"]
            except (HandoffError, TypeError, ValueError):
                raise ObserverStartGateError("observer readiness could not be validated") from None
        time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    raise ObserverStartGateError("observer readiness was not reported before deadline")


def _validate_marker(value: object) -> None:
    """Accept only the broker-generated opaque marker alphabet and length."""

    if not isinstance(value, str) or not _MARKER.fullmatch(value):
        raise ObserverStartGateError("observer run marker was invalid")
