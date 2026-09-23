"""Mode-restricted file handoffs for the six-stem load Job.

Kubernetes containers in one Pod can share an ``emptyDir``.  That is useful for
passing the disposable user's direct-grant configuration to the authenticated
load client, but it must not become a convenient way to copy administrator
credentials. This module serializes narrow, atomic ``0600`` documents for the
client-needed identity, ingress coordinates, broker verification marker,
terminal outcome, and—on a *different volume* mounted only by the broker and
PostgreSQL observer—the temporary observer login. The authenticated
client never mounts that second volume and its schema has no database fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Final
from uuid import UUID

from keycloak_identity_lifecycle import LoadTestIdentity


IDENTITY_FILE_NAME: Final[str] = "temporary-load-identity.json"
COORDINATES_FILE_NAME: Final[str] = "authenticated-load-coordinates.json"
VERIFIED_INGRESS_FILE_NAME: Final[str] = "verified-authenticated-ingress.json"
CLIENT_RESULT_FILE_NAME: Final[str] = "authenticated-load-client-result.json"
BROKER_FAILURE_FILE_NAME: Final[str] = "lifecycle-broker-failed.json"
POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME: Final[str] = "postgresql-observer-credentials.json"
_IDENTITY_SCHEMA_VERSION: Final[int] = 1
_COORDINATES_SCHEMA_VERSION: Final[int] = 1
_VERIFIED_INGRESS_SCHEMA_VERSION: Final[int] = 1
_RESULT_SCHEMA_VERSION: Final[int] = 1
_BROKER_FAILURE_SCHEMA_VERSION: Final[int] = 1
_POSTGRESQL_OBSERVER_CREDENTIALS_SCHEMA_VERSION: Final[int] = 1
# The observer report includes exact run-scoped object keys and SHA-256 values
# for three sources and 18 stems. Keep the IPC document firmly bounded while
# leaving room for that integrity evidence; ordinary identity/coordinate
# handoffs remain far smaller than this ceiling.
_MAX_HANDOFF_BYTES: Final[int] = 16 * 1024
_VALID_OUTCOMES: Final[frozenset[str]] = frozenset({"succeeded", "failed"})
_RUN_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{8,24}$")
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_SIX_STEM_MODE: Final[str] = "6-stems"
_LOAD_JOB_COUNT: Final[int] = 3
_MAX_SOURCE_BYTES: Final[int] = 256 * 1024 * 1024


class HandoffError(RuntimeError):
    """A safe handoff failure that never includes file content or credentials."""


@dataclass(frozen=True)
class TemporaryLoadClientIdentity:
    """The only identity values the authenticated load client may receive."""

    run_marker: str
    client_id: str
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class SubmittedLoadJobCoordinate:
    """One server-issued Job coordinate the broker must independently verify.

    This is private run-correlation data, not an authority grant. The later
    broker treats it as an untrusted claim: it must re-read each Job through
    the temporary user's normal owner-bound API before minting any observer
    capability from it.
    """

    ordinal: int
    job_id: str = field(repr=False)
    source_filename: str
    source_size_bytes: int
    source_sha256: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticatedLoadCoordinates:
    """The exact non-secret coordinates produced by the three uploads.

    ``subject`` and Job IDs deliberately have no default dataclass
    representation. They are private durable-work identifiers, not useful
    progress-log data. The memory-backed handoff holds them only until the
    broker verifies or removes the run.
    """

    run_marker: str
    subject: str = field(repr=False)
    stem_mode: str
    jobs: tuple[SubmittedLoadJobCoordinate, ...] = field(repr=False)


@dataclass(frozen=True)
class VerifiedAuthenticatedIngress:
    """A broker-only proof marker with no Job ID, subject, token, or object key.

    The marker is deliberately less detailed than the coordinate claim. Its
    existence says only that the broker independently verified the fixed
    three-job ingress contract for this run marker. It is neither a processing
    completion marker nor an authority grant for the separate evidence observer.
    """

    run_marker: str
    stem_mode: str
    job_count: int


@dataclass(frozen=True)
class TemporaryPostgreSQLObserverCredentials:
    """One broker-minted login handed only to the durable-state observer."""

    run_marker: str
    username: str
    password: str = field(repr=False)
    function_schema: str
    function_name: str


def prepare_empty_handoff_directory(directory: Path) -> None:
    """Require a real, empty, Pod-local directory before provisioning an identity.

    The Job manifest mounts the same ``emptyDir`` into exactly two non-root
    containers—the broker and authenticated client—running under one UID.
    Kubernetes can create an
    ``emptyDir`` with harmless other-user read/execute directory bits; the
    sensitive files themselves are always ``0600``.  Other-user *write*
    access is forbidden, because it could replace the handoff. A stale control
    file also makes the broker fail closed instead of reusing a credential from
    an interrupted Pod.
    """

    try:
        metadata = directory.lstat()
    except FileNotFoundError as error:
        raise HandoffError("load identity handoff directory was absent") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HandoffError("load identity handoff path was not a real directory")
    if metadata.st_mode & stat.S_IWOTH:
        raise HandoffError("load identity handoff directory allowed other-user writes")

    for path in (
        directory / IDENTITY_FILE_NAME,
        directory / COORDINATES_FILE_NAME,
        directory / VERIFIED_INGRESS_FILE_NAME,
        directory / CLIENT_RESULT_FILE_NAME,
        directory / BROKER_FAILURE_FILE_NAME,
    ):
        if path.exists() or path.is_symlink():
            raise HandoffError("load identity handoff directory contained stale control data")


def prepare_empty_postgresql_observer_credentials_directory(directory: Path) -> None:
    """Check the observer-only tmpfs before creating a temporary DB role.

    This must be a distinct volume from the authenticated client's handoff.
    The preflight rejects stale credentials and unsafe paths before the
    administrator transaction begins, avoiding an orphaned live role if a
    prior Pod left unexpected data behind.
    """

    try:
        metadata = directory.lstat()
    except FileNotFoundError as error:
        raise HandoffError("PostgreSQL observer credential directory was absent") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HandoffError("PostgreSQL observer credential path was not a real directory")
    if metadata.st_mode & stat.S_IWOTH:
        raise HandoffError("PostgreSQL observer credential directory allowed other-user writes")
    for path in (
        directory / POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME,
        directory / f".{POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME}.tmp",
    ):
        if path.exists() or path.is_symlink():
            raise HandoffError("PostgreSQL observer credential directory contained stale data")


def write_postgresql_observer_credentials(
    directory: Path, *, credentials: TemporaryPostgreSQLObserverCredentials
) -> Path:
    """Atomically hand off the observer password after DB grant verification."""

    _validate_postgresql_observer_credentials(credentials)
    destination = directory / POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise HandoffError("PostgreSQL observer credential handoff already existed")
    _atomic_private_json_write(
        destination,
        {
            "schema_version": _POSTGRESQL_OBSERVER_CREDENTIALS_SCHEMA_VERSION,
            "run_marker": credentials.run_marker,
            "username": credentials.username,
            "password": credentials.password,
            "function_schema": credentials.function_schema,
            "function_name": credentials.function_name,
        },
    )
    return destination


def read_postgresql_observer_credentials(
    directory: Path,
) -> TemporaryPostgreSQLObserverCredentials:
    """Read the strict password document from the observer-only tmpfs."""

    payload = _read_private_json(
        directory / POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME,
        purpose="PostgreSQL observer credential handoff",
    )
    expected_keys = {
        "schema_version",
        "run_marker",
        "username",
        "password",
        "function_schema",
        "function_name",
    }
    if (
        set(payload) != expected_keys
        or payload.get("schema_version") != _POSTGRESQL_OBSERVER_CREDENTIALS_SCHEMA_VERSION
    ):
        raise HandoffError("PostgreSQL observer credential schema was invalid")
    credentials = TemporaryPostgreSQLObserverCredentials(
        run_marker=payload.get("run_marker"),
        username=payload.get("username"),
        password=payload.get("password"),
        function_schema=payload.get("function_schema"),
        function_name=payload.get("function_name"),
    )
    try:
        _validate_postgresql_observer_credentials(credentials)
    except (TypeError, ValueError) as error:
        raise HandoffError("PostgreSQL observer credential values were invalid") from error
    return credentials


def remove_postgresql_observer_credentials(directory: Path) -> None:
    """Unlink only the known observer credential files during broker teardown."""

    for path in (
        directory / POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME,
        directory / f".{POSTGRESQL_OBSERVER_CREDENTIALS_FILE_NAME}.tmp",
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise HandoffError("PostgreSQL observer credential cleanup found an unexpected file type")
        path.unlink()


def _validate_postgresql_observer_credentials(
    credentials: TemporaryPostgreSQLObserverCredentials,
) -> None:
    """Enforce fixed run-derived role/function names and a bounded password."""

    if (
        not isinstance(credentials, TemporaryPostgreSQLObserverCredentials)
        or not isinstance(credentials.run_marker, str)
        or not _RUN_MARKER_PATTERN.fullmatch(credentials.run_marker)
        or not isinstance(credentials.username, str)
        or credentials.username != f"clouddsp_six_stem_observer_{credentials.run_marker}"
        or not isinstance(credentials.password, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", credentials.password)
        or credentials.function_schema != "public"
        or credentials.function_name != f"clouddsp_six_stem_observe_{credentials.run_marker}"
    ):
        raise ValueError("PostgreSQL observer credential contract was invalid")


def write_temporary_load_identity(directory: Path, identity: LoadTestIdentity) -> Path:
    """Atomically write only the client-needed temporary direct-grant fields."""

    payload = {
        "schema_version": _IDENTITY_SCHEMA_VERSION,
        "run_marker": identity.run_marker,
        "client_id": identity.client_id,
        "username": identity.username,
        "password": identity.password,
    }
    destination = directory / IDENTITY_FILE_NAME
    _atomic_private_json_write(destination, payload)
    return destination


def read_temporary_load_identity(directory: Path) -> TemporaryLoadClientIdentity:
    """Read and validate the one small credential handoff needed by the load client."""

    payload = _read_private_json(directory / IDENTITY_FILE_NAME, purpose="load identity handoff")
    expected_keys = {"schema_version", "run_marker", "client_id", "username", "password"}
    if set(payload) != expected_keys or payload.get("schema_version") != _IDENTITY_SCHEMA_VERSION:
        raise HandoffError("load identity handoff schema was invalid")
    values = {key: payload[key] for key in expected_keys - {"schema_version"}}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise HandoffError("load identity handoff values were invalid")
    return TemporaryLoadClientIdentity(
        run_marker=values["run_marker"],
        client_id=values["client_id"],
        username=values["username"],
        password=values["password"],
    )


def wait_for_temporary_load_identity(
    directory: Path, *, timeout_seconds: float = 12 * 60
) -> TemporaryLoadClientIdentity:
    """Wait for the broker to publish the client-only identity handoff.

    The observer's queue/KEDA preflight runs before Keycloak provisioning, so
    the client container can start well before this file exists. A bounded
    wait coordinates ordinary containers in one Job Pod without a Kubernetes
    API watch or a second privileged init container.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    deadline = time.monotonic() + timeout_seconds
    identity_path = directory / IDENTITY_FILE_NAME
    while time.monotonic() < deadline:
        if broker_failure_observed(directory):
            raise HandoffError("lifecycle broker failed before identity handoff")
        if identity_path.exists() or identity_path.is_symlink():
            return read_temporary_load_identity(directory)
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    raise HandoffError("lifecycle broker did not publish the temporary identity before deadline")


def write_authenticated_load_coordinates(
    directory: Path, *, coordinates: AuthenticatedLoadCoordinates
) -> Path:
    """Atomically publish all-or-nothing ingress coordinates for broker review.

    The client writes this only after all three ordinary Job API requests and
    presigned uploads succeed. A partial client failure therefore has no
    coordinate file that the broker could mistake for a complete run.
    """

    normalized = _validated_authenticated_load_coordinates(coordinates)
    destination = directory / COORDINATES_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise HandoffError("authenticated load coordinates already existed")
    _atomic_private_json_write(
        destination,
        {
            "schema_version": _COORDINATES_SCHEMA_VERSION,
            "run_marker": normalized.run_marker,
            "subject": normalized.subject,
            "stem_mode": normalized.stem_mode,
            "jobs": [
                {
                    "ordinal": job.ordinal,
                    "job_id": job.job_id,
                    "source_filename": job.source_filename,
                    "source_size_bytes": job.source_size_bytes,
                    "source_sha256": job.source_sha256,
                }
                for job in normalized.jobs
            ],
        },
    )
    return destination


def read_authenticated_load_coordinates(directory: Path) -> AuthenticatedLoadCoordinates:
    """Read strict complete coordinates; malformed or partial data fails closed."""

    payload = _read_private_json(
        directory / COORDINATES_FILE_NAME, purpose="authenticated load coordinates"
    )
    expected_keys = {"schema_version", "run_marker", "subject", "stem_mode", "jobs"}
    if set(payload) != expected_keys or payload.get("schema_version") != _COORDINATES_SCHEMA_VERSION:
        raise HandoffError("authenticated load coordinate schema was invalid")
    jobs_value = payload.get("jobs")
    if not isinstance(jobs_value, list):
        raise HandoffError("authenticated load coordinate jobs were invalid")
    coordinates = AuthenticatedLoadCoordinates(
        run_marker=payload.get("run_marker"),
        subject=payload.get("subject"),
        stem_mode=payload.get("stem_mode"),
        jobs=tuple(
            SubmittedLoadJobCoordinate(
                ordinal=item.get("ordinal"),
                job_id=item.get("job_id"),
                source_filename=item.get("source_filename"),
                source_size_bytes=item.get("source_size_bytes"),
                source_sha256=item.get("source_sha256"),
            )
            for item in jobs_value
            if isinstance(item, dict)
        ),
    )
    # A non-object list item must not be silently discarded.  Its mismatch
    # against the required three coordinates makes the handoff invalid.
    if len(coordinates.jobs) != len(jobs_value):
        raise HandoffError("authenticated load coordinate jobs were invalid")
    try:
        return _validated_authenticated_load_coordinates(coordinates)
    except (TypeError, ValueError) as error:
        raise HandoffError("authenticated load coordinates were invalid") from error


def wait_for_authenticated_load_coordinates(
    directory: Path, *, timeout_seconds: float
) -> AuthenticatedLoadCoordinates:
    """Wait for one complete private coordinate claim before broker verification.

    The authenticated client writes its coordinates with ``os.replace`` only
    after all three normal API/form uploads finish. If it exits earlier, a
    small failure marker wakes the broker promptly so cleanup does not wait
    out the full processing deadline. Neither marker grants observer scope.
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    coordinate_path = directory / COORDINATES_FILE_NAME
    result_path = directory / CLIENT_RESULT_FILE_NAME
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if coordinate_path.exists() or coordinate_path.is_symlink():
            return read_authenticated_load_coordinates(directory)
        if result_path.exists() or result_path.is_symlink():
            outcome = read_client_outcome(directory)
            if outcome == "failed":
                raise HandoffError("authenticated load client failed before publishing coordinates")
            raise HandoffError("authenticated load client completed without publishing coordinates")
        time.sleep(0.2)
    raise HandoffError("authenticated load client did not publish coordinates before deadline")


def write_verified_authenticated_ingress(
    directory: Path, *, coordinates: AuthenticatedLoadCoordinates
) -> Path:
    """Publish minimal broker-only proof after owner-bound API verification.

    The caller must invoke this only after ``owner_bound_job_verifier`` accepts
    every coordinate. Persisting just the fixed shape and marker allows a
    separate evidence observer to wait for this boundary without
    receiving private Job IDs, the subject, a checksum, or any credential.
    """

    normalized = _validated_authenticated_load_coordinates(coordinates)
    destination = directory / VERIFIED_INGRESS_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise HandoffError("verified authenticated ingress marker already existed")
    _atomic_private_json_write(
        destination,
        {
            "schema_version": _VERIFIED_INGRESS_SCHEMA_VERSION,
            "run_marker": normalized.run_marker,
            "stem_mode": _SIX_STEM_MODE,
            "job_count": _LOAD_JOB_COUNT,
        },
    )
    return destination


def read_verified_authenticated_ingress(directory: Path) -> VerifiedAuthenticatedIngress:
    """Read the minimal marker without turning it into a completion signal."""

    payload = _read_private_json(
        directory / VERIFIED_INGRESS_FILE_NAME, purpose="verified authenticated ingress marker"
    )
    expected_keys = {"schema_version", "run_marker", "stem_mode", "job_count"}
    if set(payload) != expected_keys or payload.get("schema_version") != _VERIFIED_INGRESS_SCHEMA_VERSION:
        raise HandoffError("verified authenticated ingress marker schema was invalid")
    marker = VerifiedAuthenticatedIngress(
        run_marker=payload.get("run_marker"),
        stem_mode=payload.get("stem_mode"),
        job_count=payload.get("job_count"),
    )
    if (
        not isinstance(marker.run_marker, str)
        or not _RUN_MARKER_PATTERN.fullmatch(marker.run_marker)
        or marker.stem_mode != _SIX_STEM_MODE
        or isinstance(marker.job_count, bool)
        or marker.job_count != _LOAD_JOB_COUNT
    ):
        raise HandoffError("verified authenticated ingress marker was invalid")
    return marker


def write_client_outcome(directory: Path, *, outcome: str) -> Path:
    """Atomically publish one minimal terminal outcome after the client exits its work."""

    if outcome not in _VALID_OUTCOMES:
        raise ValueError("outcome must be succeeded or failed")
    destination = directory / CLIENT_RESULT_FILE_NAME
    _atomic_private_json_write(
        destination,
        {"schema_version": _RESULT_SCHEMA_VERSION, "outcome": outcome},
    )
    return destination


def wait_for_client_outcome(directory: Path, *, timeout_seconds: float) -> str:
    """Wait for an atomically-written client terminal marker with a finite deadline."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    result_path = directory / CLIENT_RESULT_FILE_NAME
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if result_path.exists() or result_path.is_symlink():
            return read_client_outcome(directory)
        # A brief poll is enough because the authenticated client writes one atomic
        # file only at its terminal state.  It consumes no service/API calls.
        time.sleep(0.2)
    raise HandoffError("authenticated load client did not publish an outcome before deadline")


def read_client_outcome(directory: Path) -> str:
    """Parse one safe, bounded success/failure marker without service details."""

    payload = _read_private_json(
        directory / CLIENT_RESULT_FILE_NAME,
        purpose="authenticated load-client outcome",
    )
    if set(payload) != {"schema_version", "outcome"}:
        raise HandoffError("authenticated load-client outcome schema was invalid")
    if payload.get("schema_version") != _RESULT_SCHEMA_VERSION:
        raise HandoffError("authenticated load-client outcome version was invalid")
    outcome = payload.get("outcome")
    if outcome not in _VALID_OUTCOMES:
        raise HandoffError("authenticated load-client outcome was invalid")
    return outcome


def write_broker_failure_marker(directory: Path) -> Path:
    """Wake a waiting peer with one fixed, credential-free failure document.

    The broker publishes this to the client handoff and observer report volumes
    only after its own cleanup. It contains no run marker, user, Job ID, URL,
    token, password, or exception text. An unsafe mount must still fail closed.
    """

    try:
        metadata = directory.lstat()
    except OSError:
        raise HandoffError("broker failure handoff directory was absent") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_mode & stat.S_IWOTH
    ):
        raise HandoffError("broker failure handoff directory was unsafe")
    destination = directory / BROKER_FAILURE_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise HandoffError("broker failure marker already existed")
    _atomic_private_json_write(
        destination,
        {"schema_version": _BROKER_FAILURE_SCHEMA_VERSION, "status": "failed"},
    )
    return destination


def broker_failure_observed(directory: Path) -> bool:
    """Read a broker failure signal without disclosing any exception detail."""

    path = directory / BROKER_FAILURE_FILE_NAME
    if not path.exists() and not path.is_symlink():
        return False
    payload = _read_private_json(path, purpose="lifecycle broker failure marker")
    if payload != {"schema_version": _BROKER_FAILURE_SCHEMA_VERSION, "status": "failed"}:
        raise HandoffError("lifecycle broker failure marker was invalid")
    return True


def remove_handoff_files(directory: Path) -> None:
    """Remove only this run's known private handoff files during teardown."""

    for path in (
        directory / IDENTITY_FILE_NAME,
        directory / COORDINATES_FILE_NAME,
        directory / VERIFIED_INGRESS_FILE_NAME,
        directory / CLIENT_RESULT_FILE_NAME,
        directory / BROKER_FAILURE_FILE_NAME,
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise HandoffError("load identity handoff cleanup found an unexpected file type")
        path.unlink()


def _validated_authenticated_load_coordinates(
    coordinates: AuthenticatedLoadCoordinates,
) -> AuthenticatedLoadCoordinates:
    """Enforce the fixed three-job contract before a file crosses containers."""

    if not isinstance(coordinates.run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(
        coordinates.run_marker
    ):
        raise ValueError("load run marker was invalid")
    subject = _canonical_uuid(coordinates.subject)
    if coordinates.stem_mode != _SIX_STEM_MODE:
        raise ValueError("load stem mode was invalid")
    if not isinstance(coordinates.jobs, tuple) or len(coordinates.jobs) != _LOAD_JOB_COUNT:
        raise ValueError("load job coordinate count was invalid")
    normalized_jobs: list[SubmittedLoadJobCoordinate] = []
    for expected_ordinal, job in enumerate(coordinates.jobs, start=1):
        if not isinstance(job, SubmittedLoadJobCoordinate) or job.ordinal != expected_ordinal:
            raise ValueError("load job coordinate ordinal was invalid")
        job_id = _canonical_uuid(job.job_id)
        expected_filename = f"six-stem-load-{coordinates.run_marker}-{expected_ordinal}.wav"
        if job.source_filename != expected_filename:
            raise ValueError("load job coordinate filename was invalid")
        if (
            isinstance(job.source_size_bytes, bool)
            or not isinstance(job.source_size_bytes, int)
            or not 1 <= job.source_size_bytes <= _MAX_SOURCE_BYTES
        ):
            raise ValueError("load job coordinate source size was invalid")
        if not isinstance(job.source_sha256, str) or not _SHA256_PATTERN.fullmatch(job.source_sha256):
            raise ValueError("load job coordinate checksum was invalid")
        normalized_jobs.append(
            SubmittedLoadJobCoordinate(
                ordinal=expected_ordinal,
                job_id=job_id,
                source_filename=expected_filename,
                source_size_bytes=job.source_size_bytes,
                source_sha256=job.source_sha256,
            )
        )
    if len({job.job_id for job in normalized_jobs}) != _LOAD_JOB_COUNT:
        raise ValueError("load job coordinate identifiers were duplicated")
    if len({job.source_sha256 for job in normalized_jobs}) != _LOAD_JOB_COUNT:
        raise ValueError("load job coordinate checksums were duplicated")
    return AuthenticatedLoadCoordinates(
        run_marker=coordinates.run_marker,
        subject=subject,
        stem_mode=_SIX_STEM_MODE,
        jobs=tuple(normalized_jobs),
    )


def _canonical_uuid(value: object) -> str:
    """Reject non-string, alternate, or noncanonical UUID spellings."""

    if not isinstance(value, str):
        raise ValueError("UUID was not text")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("UUID was invalid") from error
    if canonical != value:
        raise ValueError("UUID was noncanonical")
    return canonical


def _atomic_private_json_write(destination: Path, payload: dict[str, object]) -> None:
    """Write a bounded JSON document without following a pre-existing file path."""

    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_HANDOFF_BYTES:
        raise HandoffError("load identity handoff exceeded its bounded size")
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as error:
        raise HandoffError("load identity handoff temporary file already existed") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        # rename(2) atomically replaces only the final path.  The load client
        # sees either no file or the complete 0600 JSON document, never a
        # partial password while the broker is writing it.
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_private_json(path: Path, *, purpose: str) -> dict[str, object]:
    """Read a regular 0600-ish bounded handoff file without exposing its content on error."""

    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise HandoffError(f"{purpose} was absent") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise HandoffError(f"{purpose} was not a regular file")
    if metadata.st_mode & 0o077:
        raise HandoffError(f"{purpose} allowed group or other-user access")
    try:
        with path.open("rb") as input_file:
            encoded = input_file.read(_MAX_HANDOFF_BYTES + 1)
    except OSError as error:
        raise HandoffError(f"{purpose} could not be read") from error
    if len(encoded) > _MAX_HANDOFF_BYTES:
        raise HandoffError(f"{purpose} exceeded its bounded size")
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandoffError(f"{purpose} was invalid JSON") from error
    if not isinstance(payload, dict):
        raise HandoffError(f"{purpose} JSON shape was invalid")
    return payload
