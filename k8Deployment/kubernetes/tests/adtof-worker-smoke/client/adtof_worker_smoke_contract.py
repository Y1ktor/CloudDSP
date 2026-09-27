"""Define the dependency-free contract for the future ADTOF worker smoke client.

This module is intentionally *not* an S3, PostgreSQL, RabbitMQ, ADTOF-model,
Docker, or Kubernetes client. It centralizes the fixed local routes, restricted
credential names, and typed capabilities that later adapters may implement.
Keeping that contract separate prevents a future one-shot smoke Job from
silently changing its test coordinate, calling a browser route, or acquiring a
generic database/object-store interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from adtof_worker_smoke_fixture import (
    MIDI_KEY,
    MIDI_CONTENT_TYPE,
    SMOKE_EVENT_ID,
    SMOKE_JOB_ID,
    STEM_KEY,
    TEMPO_KEY,
    TEMPO_CONTENT_TYPE,
    UPLOADS_BUCKET,
    WAV_CONTENT_TYPE,
    ControlledDrumWav,
)


# These routes are source-controlled rather than freely configurable Job
# settings. A smoke client may contact only the cluster-internal PostgreSQL and
# MinIO Services; it must never be redirected to localhost, a Mac port-forward,
# an AWS endpoint, a public ingress, or another namespace.
PRIVATE_POSTGRESQL_HOST = "clouddsp-postgresql.clouddsp-data.svc"
PRIVATE_POSTGRESQL_PORT = 5432
PRIVATE_MINIO_ENDPOINT_URL = "http://clouddsp-minio.clouddsp-data.svc:9000"
MINIO_REGION = "us-east-1"

# The names/values below match the two applied restricted smoke Secrets. The
# password and secret key intentionally are not source constants: a local
# operator may rotate either Secret without changing the identity or policy.
EXPECTED_DATABASE_NAME = "clouddsp_job_api"
EXPECTED_DATABASE_USERNAME = "clouddsp-adtof-worker-smoke"
EXPECTED_S3_ACCESS_KEY = "clouddsp-adtof-worker-smoke"

# The future orchestrator needs enough time for dispatcher polling and one CPU
# ADTOF attempt, but it must still have a finite, reviewable upper bound. This
# module merely validates the setting; it does not sleep or start a timeout.
DEFAULT_OBSERVATION_TIMEOUT_SECONDS = 12 * 60
MIN_OBSERVATION_TIMEOUT_SECONDS = 60
MAX_OBSERVATION_TIMEOUT_SECONDS = 15 * 60

# These strings name the fixed, administrator-owned database capabilities.
# They are not arbitrary SQL supplied by a smoke Job environment variable.
PREPARE_FUNCTION = "public.clouddsp_adtof_worker_smoke_prepare"
OBSERVE_FUNCTION = "public.clouddsp_adtof_worker_smoke_observe"
CLEANUP_FUNCTION = "public.clouddsp_adtof_worker_smoke_cleanup"

# Fixed key/type pairs mirror the reviewed MinIO policy and the deployed ADTOF
# worker output contract. Keeping this map source-controlled means a future S3
# adapter cannot call a stored text blob or an unrelated WAV a verified output.
EXPECTED_CONTENT_TYPES_BY_KEY = {
    STEM_KEY: WAV_CONTENT_TYPE,
    MIDI_KEY: MIDI_CONTENT_TYPE,
    TEMPO_KEY: TEMPO_CONTENT_TYPE,
}


class ADTOFWorkerSmokeConfigurationError(RuntimeError):
    """Reject unsafe/missing client configuration without showing secret values."""


class ADTOFWorkerSmokeContractError(RuntimeError):
    """Reject a future adapter result that violates the fixed smoke contract."""


def _required_secret(environment: Mapping[str, str], name: str) -> str:
    """Read one non-empty secret without including its value in an error."""

    value = environment.get(name)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ADTOFWorkerSmokeConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _fixed_environment_value(
    environment: Mapping[str, str], *, name: str, expected: str
) -> str:
    """Accept an omitted/exact route setting but reject every redirect attempt."""

    value = environment.get(name, expected)
    if value != expected:
        raise ADTOFWorkerSmokeConfigurationError(
            f"{name} must equal its reviewed private smoke-client value."
        )
    return expected


def _bounded_timeout(environment: Mapping[str, str]) -> int:
    """Parse the one future observation bound before it controls a wait loop."""

    raw_value = environment.get(
        "ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS",
        str(DEFAULT_OBSERVATION_TIMEOUT_SECONDS),
    )
    try:
        timeout = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ADTOFWorkerSmokeConfigurationError(
            "ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS must be an integer."
        ) from error
    if not MIN_OBSERVATION_TIMEOUT_SECONDS <= timeout <= MAX_OBSERVATION_TIMEOUT_SECONDS:
        raise ADTOFWorkerSmokeConfigurationError(
            "ADTOF_WORKER_SMOKE_TIMEOUT_SECONDS is outside the reviewed bound."
        )
    return timeout


@dataclass(frozen=True)
class ADTOFWorkerSmokeSettings:
    """The future Job's fixed private routes and restricted credential values.

    ``repr=False`` keeps the database password and S3 secret key out of normal
    exception/debug output. This data class opens no socket and creates no
    Boto3/Psycopg client; a later focused adapter must consume it explicitly.
    """

    database_host: str
    database_port: int
    database_name: str
    database_username: str
    database_password: str = field(repr=False)
    minio_endpoint_url: str = PRIVATE_MINIO_ENDPOINT_URL
    minio_region: str = MINIO_REGION
    minio_access_key: str = ""
    minio_secret_key: str = field(default="", repr=False)
    observation_timeout_seconds: int = DEFAULT_OBSERVATION_TIMEOUT_SECONDS

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> "ADTOFWorkerSmokeSettings":
        """Load only Secret values plus exact reviewed private route settings.

        Passing an explicit mapping makes this code deterministic and unit
        testable. A later CLI entrypoint may pass ``os.environ`` but must not
        add arbitrary host, bucket, stage, object-key, queue, or function-name
        configuration.
        """

        _fixed_environment_value(
            environment,
            name="ADTOF_WORKER_SMOKE_DB_HOST",
            expected=PRIVATE_POSTGRESQL_HOST,
        )
        _fixed_environment_value(
            environment,
            name="ADTOF_WORKER_SMOKE_DB_PORT",
            expected=str(PRIVATE_POSTGRESQL_PORT),
        )
        _fixed_environment_value(
            environment,
            name="ADTOF_WORKER_SMOKE_MINIO_ENDPOINT_URL",
            expected=PRIVATE_MINIO_ENDPOINT_URL,
        )
        _fixed_environment_value(
            environment,
            name="ADTOF_WORKER_SMOKE_MINIO_REGION",
            expected=MINIO_REGION,
        )

        database_name = _required_secret(environment, "ADTOF_WORKER_SMOKE_DB_NAME")
        database_username = _required_secret(environment, "ADTOF_WORKER_SMOKE_DB_USERNAME")
        minio_access_key = _required_secret(environment, "ADTOF_WORKER_SMOKE_S3_ACCESS_KEY")
        if database_name != EXPECTED_DATABASE_NAME:
            raise ADTOFWorkerSmokeConfigurationError("Smoke database name is not the fixed target.")
        if database_username != EXPECTED_DATABASE_USERNAME:
            raise ADTOFWorkerSmokeConfigurationError("Smoke database user is not the fixed identity.")
        if minio_access_key != EXPECTED_S3_ACCESS_KEY:
            raise ADTOFWorkerSmokeConfigurationError("Smoke S3 access key is not the fixed identity.")

        return cls(
            database_host=PRIVATE_POSTGRESQL_HOST,
            database_port=PRIVATE_POSTGRESQL_PORT,
            database_name=database_name,
            database_username=database_username,
            database_password=_required_secret(environment, "ADTOF_WORKER_SMOKE_DB_PASSWORD"),
            minio_endpoint_url=PRIVATE_MINIO_ENDPOINT_URL,
            minio_region=MINIO_REGION,
            minio_access_key=minio_access_key,
            minio_secret_key=_required_secret(environment, "ADTOF_WORKER_SMOKE_S3_SECRET_KEY"),
            observation_timeout_seconds=_bounded_timeout(environment),
        )


def _canonical_fixed_uuid(value: object, *, expected: str, field_name: str) -> str:
    """Require one exact canonical fixed UUID, not merely a parseable UUID."""

    if isinstance(value, UUID):
        value = str(value)
    if not isinstance(value, str):
        raise ADTOFWorkerSmokeContractError(f"Smoke {field_name} is invalid.")
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError) as error:
        raise ADTOFWorkerSmokeContractError(f"Smoke {field_name} is invalid.") from error
    if canonical != value or value != expected:
        raise ADTOFWorkerSmokeContractError(f"Smoke {field_name} is not the reserved coordinate.")
    return value


@dataclass(frozen=True)
class PreparedADTOFWorkerSmoke:
    """The only durable creation result a future database adapter may return."""

    job_id: str
    event_id: str

    def __post_init__(self) -> None:
        """Bind every prepared event to this smoke test's fixed coordinates."""

        _canonical_fixed_uuid(self.job_id, expected=SMOKE_JOB_ID, field_name="Job ID")
        _canonical_fixed_uuid(self.event_id, expected=SMOKE_EVENT_ID, field_name="event ID")


@dataclass(frozen=True)
class ADTOFWorkerSmokeObservation:
    """Safe durable facts from the fixed `observe()` PostgreSQL function.

    The future database adapter maps one returned row into this class. It must
    not attach a raw outbox payload, owner subject, object URL, task error, or
    unrestricted job history. A lack of a task is normal while publication or
    the deployed worker is still progressing.
    """

    publication_status: str
    published_at: datetime | None
    task_id: str | None
    task_status: str | None
    task_attempt_count: int | None
    task_lease_is_clear: bool
    task_completed_at: datetime | None
    job_status: str

    def __post_init__(self) -> None:
        """Reject malformed adapter data before the future orchestrator trusts it."""

        if self.publication_status not in {"pending", "leased", "published", "dead_lettered"}:
            raise ADTOFWorkerSmokeContractError("Smoke publication status is invalid.")
        if self.job_status not in {
            "upload_pending",
            "source_ingestion",
            "source_uploaded",
            "stem_processing",
            "midi_processing",
            "failed_incomplete_stem_fixture",
            "completed",
            "failed",
        }:
            raise ADTOFWorkerSmokeContractError("Smoke Job status is invalid.")
        if type(self.task_lease_is_clear) is not bool:
            raise ADTOFWorkerSmokeContractError("Smoke task lease fact is invalid.")
        for timestamp in (self.published_at, self.task_completed_at):
            if timestamp is not None and (
                not isinstance(timestamp, datetime) or timestamp.tzinfo is None
            ):
                raise ADTOFWorkerSmokeContractError("Smoke timestamp is invalid.")
        if self.task_id is None:
            if self.task_status is not None or self.task_attempt_count is not None:
                raise ADTOFWorkerSmokeContractError("Smoke task facts are inconsistent.")
            return
        _canonical_fixed_uuid(self.task_id, expected=self.task_id, field_name="task ID")
        if self.task_status not in {"leased", "running", "retry_scheduled", "succeeded", "failed"}:
            raise ADTOFWorkerSmokeContractError("Smoke task status is invalid.")
        if type(self.task_attempt_count) is not int or not 1 <= self.task_attempt_count <= 3:
            raise ADTOFWorkerSmokeContractError("Smoke task attempt count is invalid.")

    @property
    def is_successful_first_attempt(self) -> bool:
        """Return true only for the exact end-to-end worker success condition.

        This does not validate MIDI/tempo object bytes. A later object-verifier
        boundary must independently establish those storage facts before the
        eventual client invokes its successful-only cleanup capability.
        """

        return (
            self.publication_status == "published"
            and self.published_at is not None
            and self.task_id is not None
            and self.task_status == "succeeded"
            and self.task_attempt_count == 1
            and self.task_lease_is_clear
            and self.task_completed_at is not None
            # The restricted SQL observer emits this only for the exact
            # expected parent-finalizer error on this one-drum fixture.
            and self.job_status == "failed_incomplete_stem_fixture"
        )


@dataclass(frozen=True)
class FixedObjectEvidence:
    """Safe object fact that a later narrow S3 adapter may return after reading.

    Object bytes/metadata validation belongs to a later focused task. This
    typed evidence only prevents an adapter from reporting an arbitrary key or
    an unbounded/empty result as an ADTOF smoke output.
    """

    key: str
    content_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        """Require one of the three policy-granted keys and basic bounded facts."""

        if self.key not in {STEM_KEY, MIDI_KEY, TEMPO_KEY}:
            raise ADTOFWorkerSmokeContractError("Smoke object key is outside the fixed policy.")
        if self.content_type != EXPECTED_CONTENT_TYPES_BY_KEY.get(self.key):
            raise ADTOFWorkerSmokeContractError("Smoke object content type is invalid.")
        if type(self.size_bytes) is not int or self.size_bytes < 1:
            raise ADTOFWorkerSmokeContractError("Smoke object size is invalid.")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ADTOFWorkerSmokeContractError("Smoke object SHA-256 is invalid.")


@dataclass(frozen=True)
class VerifiedADTOFWorkerSmokeOutputs:
    """The future object adapter's two-output evidence, not an S3 client itself."""

    midi: FixedObjectEvidence
    tempo: FixedObjectEvidence

    def __post_init__(self) -> None:
        """Keep output verification tied to the two producer-owned ADTOF keys."""

        if self.midi.key != MIDI_KEY or self.tempo.key != TEMPO_KEY:
            raise ADTOFWorkerSmokeContractError("Smoke output evidence names are invalid.")


class ADTOFWorkerSmokeDatabase(Protocol):
    """The only database capability a later client composition may receive.

    An implementation calls the three fixed security-definer functions; it
    must not expose a raw SQL executor, table reader, outbox publisher, or
    transaction that wraps S3/model work.
    """

    def prepare_fixed_event(
        self, *, stem_size_bytes: int, stem_sha256: str
    ) -> PreparedADTOFWorkerSmoke:
        """Create only the fixed Job/outbox event after the controlled WAV exists."""

    def observe_fixed_event(self) -> ADTOFWorkerSmokeObservation | None:
        """Read only the fixed publication/task projection while work progresses."""

    def cleanup_fixed_success(self) -> bool:
        """Delete the fixed PostgreSQL evidence only after verified success."""


class ADTOFWorkerSmokeObjectStore(Protocol):
    """The only object-storage capability a later client composition may receive.

    An implementation uses the restricted S3 identity and must perform all S3
    calls internally. It exposes no generic bucket/key operation, bucket list,
    presign method, user-management call, or output write operation.
    """

    def assert_fixed_objects_absent(self) -> None:
        """Fail if the one controlled input or either expected output is already present."""

    def upload_controlled_drum_wav(self, fixture: ControlledDrumWav) -> FixedObjectEvidence:
        """Put only the exact fixture WAV/key/metadata allowed by the policy."""

    def read_verified_adtof_outputs(
        self, *, task_id: str, input_stem: FixedObjectEvidence
    ) -> VerifiedADTOFWorkerSmokeOutputs:
        """Read the exact outputs bound to the observed task and uploaded WAV.

        The dynamic task ID comes only from a successful fixed database
        observation; the input evidence comes only from the preceding
        post-upload MinIO proof.  Supplying both facts prevents this storage
        boundary from treating a valid-looking output from another worker
        attempt as the result of this reserved smoke run.
        """

    def delete_fixed_objects_after_success(
        self,
        *,
        observation: ADTOFWorkerSmokeObservation,
        input_stem: FixedObjectEvidence,
        outputs: VerifiedADTOFWorkerSmokeOutputs,
    ) -> None:
        """Delete only evidence already tied to successful first-attempt work.

        This is deliberately an object-only capability.  A later composition
        must arrange the separately guarded PostgreSQL cleanup call; neither
        the object adapter nor this interface can delete a durable Job/event.
        """


def fixed_storage_bucket() -> str:
    """Return the one private bucket shared by every fixed smoke operation."""

    return UPLOADS_BUCKET
