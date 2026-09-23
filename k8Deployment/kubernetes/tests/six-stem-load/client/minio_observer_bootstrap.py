"""Create one temporary, exact-object MinIO reader for the load observer.

The lifecycle broker calls this adapter only after ordinary owner-bound API
reads proved the three generated Jobs. The administrator credential is never
given to the data observer: this adapter asks the pinned MinIO ``mc`` binary
to create a run-specific S3 user and a policy listing only the 42 expected
source, stem, and MIDI object ARNs. The returned secret is handed to a separate
memory-backed file that the authenticated load client does not mount.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Final

from lifecycle_handoff import HandoffError, _atomic_private_json_write, _read_private_json
from observer_capability_contract import VerifiedLoadObserverCapabilityContract


MINIO_OBSERVER_CREDENTIALS_FILE_NAME: Final[str] = "minio-observer-credentials.json"
_SCHEMA_VERSION: Final[int] = 1
_BUCKET: Final[str] = "clouddsp-uploads"
_MINIO_ENDPOINT: Final[str] = "http://clouddsp-minio.clouddsp-data.svc:9000"
_RUN_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{8,24}$")
_PASSWORD_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_STEMS: Final[tuple[str, ...]] = ("bass", "drums", "guitar", "other", "piano", "vocals")
_MIDI_KEYS: Final[tuple[str, ...]] = (
    "bass.mid", "vocals.mid", "other.mid", "guitar.mid", "piano.mid",
    "drums.mid", "drums_bpm.json",
)


class MinIOObserverBootstrapError(RuntimeError):
    """Safe MinIO setup/teardown failure without endpoint or credential text."""


@dataclass(frozen=True)
class TemporaryMinIOObserverCredentials:
    """One run-bound MinIO identity; the password is excluded from repr/logging."""

    run_marker: str
    access_key: str
    secret_key: str = field(repr=False)


@dataclass(frozen=True)
class MinIOAdminSettings:
    """Administrator values visible only to the lifecycle-broker container."""

    root_username: str = field(repr=False)
    root_password: str = field(repr=False)
    work_directory: Path = field(repr=False)
    executable: str = "/usr/local/bin/mc"

    def __post_init__(self) -> None:
        """Pin the server, executable, and writable path to reviewed values."""

        if (
            not self.root_username
            or not self.root_password
            or self.executable != "/usr/local/bin/mc"
            or not isinstance(self.work_directory, Path)
        ):
            raise MinIOObserverBootstrapError("MinIO administrator settings were invalid")


Runner = Callable[[tuple[str, ...], Mapping[str, str], float], None]


@dataclass(frozen=True)
class MinIOObserverBootstrap:
    """Short-lived administrator adapter that provisions/revokes a read-only user."""

    settings: MinIOAdminSettings = field(repr=False)
    runner: Runner | None = field(default=None, repr=False)

    def provision(
        self, *, capability: VerifiedLoadObserverCapabilityContract
    ) -> TemporaryMinIOObserverCredentials:
        """Create the exact-key policy and user, then return its private login."""

        _validate_capability(capability)
        access_key = f"s6m{capability.run_marker}"
        policy_name = _policy_name(capability.run_marker)
        secret_key = secrets.token_urlsafe(40)
        policy_path = self.settings.work_directory / "exact-object-policy.json"
        environment: Mapping[str, str] = {}
        stage = "prepare MinIO work directory"
        policy_created = False
        user_created = False
        try:
            environment = self._prepare_mc_environment()
            stage = "write exact-object policy"
            _atomic_private_json_write(
                policy_path,
                _exact_get_policy(_expected_object_keys(capability)),
            )
            stage = "configure administrator alias"
            self._run(
                ("alias", "set", "minio-admin", _MINIO_ENDPOINT,
                 self.settings.root_username, self.settings.root_password),
                environment,
            )
            stage = "create exact-object policy"
            self._run(
                ("admin", "policy", "create", "minio-admin", policy_name,
                 str(policy_path)),
                environment,
            )
            policy_created = True
            stage = "create observer user"
            self._run(
                ("admin", "user", "add", "minio-admin", access_key, secret_key),
                environment,
            )
            user_created = True
            stage = "attach exact-object policy"
            self._run(
                ("admin", "policy", "attach", "minio-admin", policy_name,
                 f"--user={access_key}"),
                environment,
            )
            # A successful attach is followed by an administrative identity
            # lookup. Its output is discarded: the Pod log never receives the
            # user's key, policy body, password, or object coordinates.
            stage = "verify observer user"
            self._run(
                ("admin", "user", "info", "minio-admin", access_key),
                environment,
            )
        except Exception:
            # Report only this fixed operation label. mc output and exception
            # text can include credentials, endpoints, or exact object names.
            print(
                f"MinIO observer provisioning failed at stage: {stage}",
                file=sys.stderr,
            )
            # If one ordered MinIO call fails, undo only resources named by this
            # unpredictable run marker. Cleanup errors do not replace the safe
            # original failure category and are never printed.
            try:
                if environment:
                    self._revoke_named(
                        access_key=access_key,
                        policy_name=policy_name,
                        environment=environment,
                        user_created=user_created,
                        policy_created=policy_created,
                    )
            except Exception:
                pass
            raise MinIOObserverBootstrapError(
                "temporary MinIO observer provisioning failed"
            ) from None
        finally:
            try:
                policy_path.unlink(missing_ok=True)
            except OSError:
                pass
        return TemporaryMinIOObserverCredentials(
            run_marker=capability.run_marker,
            access_key=access_key,
            secret_key=secret_key,
        )

    def revoke(self, *, credentials: TemporaryMinIOObserverCredentials) -> None:
        """Detach the run policy, remove its user, and remove only its policy."""

        _validate_credentials(credentials)
        environment = self._prepare_mc_environment()
        try:
            self._run(
                ("alias", "set", "minio-admin", _MINIO_ENDPOINT,
                 self.settings.root_username, self.settings.root_password),
                environment,
            )
            self._revoke_named(
                access_key=credentials.access_key,
                policy_name=_policy_name(credentials.run_marker),
                environment=environment,
                user_created=True,
                policy_created=True,
            )
        except Exception:
            raise MinIOObserverBootstrapError(
                "temporary MinIO observer revocation failed"
            ) from None

    def _prepare_mc_environment(self) -> dict[str, str]:
        """Make mc state private and memory-backed instead of writing its home."""

        try:
            # The Job's root init container owns the emptyDir mount root and
            # intentionally leaves it root:10012 mode 0770. The non-root broker
            # may create private children there, but must not try to chmod the
            # mount root itself. Validate the Kubernetes-provided directory
            # rather than silently creating a different path if the mount is
            # missing or replaced by a symlink.
            work_metadata = self.settings.work_directory.lstat()
            if (
                not stat.S_ISDIR(work_metadata.st_mode)
                or stat.S_ISLNK(work_metadata.st_mode)
                or work_metadata.st_mode & stat.S_IWOTH
            ):
                raise MinIOObserverBootstrapError(
                    "MinIO observer work directory was unsafe"
                )
            config_directory = self.settings.work_directory / "mc-config"
            config_directory.mkdir(mode=0o700, exist_ok=True)
            config_metadata = config_directory.lstat()
            if (
                not stat.S_ISDIR(config_metadata.st_mode)
                or stat.S_ISLNK(config_metadata.st_mode)
            ):
                raise MinIOObserverBootstrapError(
                    "MinIO observer config directory was unsafe"
                )
            # This child was created by the broker UID and is its own, private
            # mc state directory; chmod is valid here unlike on the mount root.
            os.chmod(config_directory, 0o700)
        except (MinIOObserverBootstrapError, OSError):
            raise MinIOObserverBootstrapError("MinIO observer work path was unavailable") from None
        return {
            "MC_CONFIG_DIR": str(self.settings.work_directory / "mc-config"),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.settings.work_directory),
        }

    def _run(self, arguments: tuple[str, ...], environment: Mapping[str, str]) -> None:
        """Run only the pinned CLI, suppress output, and bound every admin call."""

        runner = self.runner or _subprocess_runner
        runner((self.settings.executable, *arguments), environment, 30.0)

    def _revoke_named(
        self,
        *,
        access_key: str,
        policy_name: str,
        environment: Mapping[str, str],
        user_created: bool,
        policy_created: bool,
    ) -> None:
        """Remove exact marker-derived IAM objects without touching bucket data."""

        if user_created:
            # Detach may fail if provisioning stopped before its attach step;
            # user removal is still a safe continuation because this user name
            # is generated solely from the current opaque marker.
            try:
                self._run(
                    ("admin", "policy", "detach", "minio-admin", policy_name,
                     f"--user={access_key}"),
                    environment,
                )
            except Exception:
                pass
            self._run(("admin", "user", "remove", "minio-admin", access_key), environment)
        if policy_created:
            self._run(("admin", "policy", "rm", "minio-admin", policy_name), environment)


def write_minio_observer_credentials(
    directory: Path, *, credentials: TemporaryMinIOObserverCredentials
) -> Path:
    """Atomically hand the observer its generated key after policy attachment."""

    _validate_credentials(credentials)
    _require_private_directory(directory)
    destination = directory / MINIO_OBSERVER_CREDENTIALS_FILE_NAME
    if destination.exists() or destination.is_symlink():
        raise MinIOObserverBootstrapError("MinIO observer credential already existed")
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run_marker": credentials.run_marker,
        "access_key": credentials.access_key,
        "secret_key": credentials.secret_key,
    }
    try:
        return _atomic_private_json_write(destination, payload)
    except (HandoffError, OSError):
        raise MinIOObserverBootstrapError(
            "MinIO observer credential handoff failed"
        ) from None


def read_minio_observer_credentials(
    directory: Path, *, expected_run_marker: str
) -> TemporaryMinIOObserverCredentials:
    """Read and validate a broker-created 0600 credential from a read-only mount."""

    _validate_run_marker(expected_run_marker)
    _require_private_directory(directory)
    try:
        payload = _read_private_json(
            directory / MINIO_OBSERVER_CREDENTIALS_FILE_NAME,
            purpose="MinIO observer credentials",
        )
        if set(payload) != {"schema_version", "run_marker", "access_key", "secret_key"}:
            raise MinIOObserverBootstrapError("MinIO observer credential schema was invalid")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != _SCHEMA_VERSION:
            raise MinIOObserverBootstrapError("MinIO observer credential version was invalid")
        if payload.get("run_marker") != expected_run_marker:
            raise MinIOObserverBootstrapError("MinIO observer credential belonged to another run")
        credentials = TemporaryMinIOObserverCredentials(
            run_marker=expected_run_marker,
            access_key=payload.get("access_key"),  # type: ignore[arg-type] -- validated below.
            secret_key=payload.get("secret_key"),  # type: ignore[arg-type] -- validated below.
        )
        _validate_credentials(credentials)
    except MinIOObserverBootstrapError:
        raise
    except (HandoffError, TypeError, ValueError):
        raise MinIOObserverBootstrapError(
            "MinIO observer credential could not be validated"
        ) from None
    return credentials


def prepare_empty_minio_observer_credentials_directory(directory: Path) -> None:
    """Reject stale files from a prior run before a new key is written."""

    _require_private_directory(directory)
    for path in (
        directory / MINIO_OBSERVER_CREDENTIALS_FILE_NAME,
        directory / f".{MINIO_OBSERVER_CREDENTIALS_FILE_NAME}.tmp",
    ):
        if path.exists() or path.is_symlink():
            raise MinIOObserverBootstrapError("MinIO observer handoff contained stale data")


def remove_minio_observer_credentials(directory: Path) -> None:
    """Remove only the known MinIO observer handoff files during teardown."""

    for path in (
        directory / MINIO_OBSERVER_CREDENTIALS_FILE_NAME,
        directory / f".{MINIO_OBSERVER_CREDENTIALS_FILE_NAME}.tmp",
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise MinIOObserverBootstrapError("MinIO observer cleanup found an unexpected file type")
        path.unlink()


def _exact_get_policy(object_keys: tuple[str, ...]) -> dict[str, object]:
    """Grant only GetObject (including HeadObject) on the enumerated test keys."""

    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "ReadOnlyExactSixStemLoadObjects",
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": [f"arn:aws:s3:::{_BUCKET}/{key}" for key in object_keys],
        }],
    }


def _expected_object_keys(
    capability: VerifiedLoadObserverCapabilityContract,
) -> tuple[str, ...]:
    """Build 42 exact object paths from the already owner-verified three Jobs."""

    keys: list[str] = []
    for ordinal, scope in enumerate(capability.object_scopes, start=1):
        filename = f"six-stem-load-{capability.run_marker}-{ordinal}.wav"
        keys.append(f"uploads/{scope.job_id}/{filename}")
        keys.extend(f"stems/{scope.job_id}/{stem}.wav" for stem in _STEMS)
        keys.extend(f"midi/{scope.job_id}/{name}" for name in _MIDI_KEYS)
    return tuple(keys)


def _policy_name(run_marker: str) -> str:
    """Name one MinIO policy from the validated opaque test-run marker."""

    _validate_run_marker(run_marker)
    return f"clouddsp-six-stem-observer-{run_marker}"


def _validate_capability(capability: object) -> None:
    """Refuse broad, malformed, or non-six-stem object scope inputs."""

    if (
        not isinstance(capability, VerifiedLoadObserverCapabilityContract)
        or not isinstance(capability.run_marker, str)
        or not _RUN_MARKER_PATTERN.fullmatch(capability.run_marker)
        or capability.stem_mode != "6-stems"
        or len(capability.object_scopes) != 3
        or len(capability.job_ids) != 3
    ):
        raise MinIOObserverBootstrapError("MinIO observer scope was invalid")
    observed: list[str] = []
    for ordinal, scope in enumerate(capability.object_scopes, start=1):
        expected_filename = f"six-stem-load-{capability.run_marker}-{ordinal}.wav"
        if (
            scope.job_id not in capability.job_ids
            or scope.bucket != _BUCKET
            or scope.source_filename != expected_filename
            or scope.source_object_key != f"uploads/{scope.job_id}/{expected_filename}"
            or scope.stems_prefix != f"stems/{scope.job_id}/"
            or scope.midi_prefix != f"midi/{scope.job_id}/"
        ):
            raise MinIOObserverBootstrapError("MinIO observer object scope was invalid")
        observed.append(scope.job_id)
    if len(set(observed)) != 3 or set(observed) != set(capability.job_ids):
        raise MinIOObserverBootstrapError("MinIO observer Job identifiers were invalid")


def _validate_credentials(credentials: TemporaryMinIOObserverCredentials) -> None:
    """Accept only one marker-derived S3 identity and bounded random secret."""

    _validate_run_marker(credentials.run_marker)
    if (
        credentials.access_key != f"s6m{credentials.run_marker}"
        or not isinstance(credentials.secret_key, str)
        or not _PASSWORD_PATTERN.fullmatch(credentials.secret_key)
    ):
        raise MinIOObserverBootstrapError("MinIO observer credential values were invalid")


def _validate_run_marker(run_marker: object) -> None:
    """Require the opaque lifecycle marker before deriving IAM filenames."""

    if not isinstance(run_marker, str) or not _RUN_MARKER_PATTERN.fullmatch(run_marker):
        raise MinIOObserverBootstrapError("MinIO observer run marker was invalid")


def _require_private_directory(directory: Path) -> None:
    """Require a real non-world-writable emptyDir path, not a symlink."""

    try:
        metadata = directory.lstat()
    except FileNotFoundError as error:
        raise MinIOObserverBootstrapError("MinIO observer handoff path was absent") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_mode & stat.S_IWOTH:
        raise MinIOObserverBootstrapError("MinIO observer handoff path was unsafe")


def _subprocess_runner(
    arguments: tuple[str, ...], environment: Mapping[str, str], timeout: float
) -> None:
    """Invoke mc without shell parsing and discard all output/diagnostics."""

    subprocess.run(
        arguments,
        check=True,
        close_fds=True,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
