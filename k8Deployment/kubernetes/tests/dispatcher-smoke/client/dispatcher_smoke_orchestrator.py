"""Create one controlled normal upload for the dispatcher smoke verifier.

This module owns the *arrangement* of the integration test, not the
dispatcher assertion itself.  It creates a short-lived Keycloak user, asks the
normal Job API for a direct-upload contract, performs that exact presigned
multipart POST through private MinIO DNS, and waits for upload-intake to
durably create the Demucs outbox event.  It then gives only the event's stable
IDs to :mod:`dispatcher_smoke_client`, which verifies PostgreSQL publication
and the restricted AMQP delivery.

The distinction is deliberate.  Seeding PostgreSQL or publishing directly to
RabbitMQ would bypass the production-like browser/API/MinIO/intake route and
would no longer prove the end-to-end handoff.  Conversely, this module never
creates a Demucs request itself; only upload-intake and the dispatcher may do
that.
"""

from __future__ import annotations

import json
import os
import secrets
import struct
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from dispatcher_smoke_client import (
    DEFAULT_TIMEOUT_SECONDS,
    LOCAL_UPLOADS_BUCKET,
    MAX_TIMEOUT_SECONDS,
    DispatcherSmokeAmqpSettings,
    DispatcherSmokeAssertionError,
    DispatcherSmokeConfigurationError,
    DispatcherSmokeDatabaseSettings,
    DispatcherSmokeInfrastructureError,
    ExpectedDemucsRequested,
    run_preflight,
    run_verification,
)


DEMUCS_STAGE = "demucs"
DEMUCS_EVENT_TYPE = "demucs.requested"
CONTROLLED_STEM_MODE = "4-stems"


class DispatcherSmokeOrchestrationError(RuntimeError):
    """Raise a fixed, log-safe category for normal-path orchestration faults."""


class ControlledEventNotReady(DispatcherSmokeOrchestrationError):
    """Represent only the short normal delay before intake commits its event."""


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read one required setting without including its value in diagnostics."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DispatcherSmokeConfigurationError(f"Required environment variable {name} is absent.")
    return value


def _bounded_integer(*, name: str, value: str, minimum: int, maximum: int) -> int:
    """Parse a bounded port/timeout before the value can control I/O."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise DispatcherSmokeConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= parsed <= maximum:
        raise DispatcherSmokeConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return parsed


def _canonical_uuid(value: object, *, label: str) -> str:
    """Accept canonical lowercase UUID text before it scopes cleanup or SQL."""

    if not isinstance(value, str):
        raise DispatcherSmokeOrchestrationError(f"{label} was missing from the normal upload flow.")
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as error:
        raise DispatcherSmokeOrchestrationError(f"{label} was invalid in the normal upload flow.") from error
    if str(parsed) != value:
        raise DispatcherSmokeOrchestrationError(f"{label} was not canonical in the normal upload flow.")
    return value


@dataclass(frozen=True)
class DispatcherSmokeOrchestratorSettings:
    """Private routes and credentials needed only by the disposable test Job.

    The real application Pods do not receive this combination of Keycloak,
    PostgreSQL, and MinIO administrator credentials.  A future smoke Job will
    mount them from existing local-only Secrets and this class keeps them out
    of ``repr`` output as a second guard against accidental diagnostic leaks.
    """

    keycloak_internal_base_url: str
    job_api_internal_base_url: str
    minio_internal_base_url: str
    keycloak_admin_realm: str
    clouddsp_realm: str
    job_api_client_id: str
    pod_name: str
    keycloak_admin_username: str
    keycloak_admin_password: str = field(repr=False)
    postgresql_host: str = "clouddsp-postgresql.clouddsp-data.svc"
    postgresql_port: int = 5432
    postgresql_database: str = "clouddsp_job_api"
    postgresql_admin_username: str = ""
    postgresql_admin_password: str = field(default="", repr=False)
    minio_root_username: str = ""
    minio_root_password: str = field(default="", repr=False)
    uploads_bucket: str = LOCAL_UPLOADS_BUCKET
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "DispatcherSmokeOrchestratorSettings":
        """Load only the fixed local routes expected by the future smoke Job."""

        settings = cls(
            keycloak_internal_base_url=_required_environment(
                "KEYCLOAK_INTERNAL_BASE_URL", default="http://clouddsp-keycloak:8080"
            ).rstrip("/"),
            job_api_internal_base_url=_required_environment(
                "JOB_API_INTERNAL_BASE_URL",
                default="http://clouddsp-job-api.clouddsp-app.svc:80",
            ).rstrip("/"),
            minio_internal_base_url=_required_environment(
                "MINIO_INTERNAL_BASE_URL", default="http://clouddsp-minio:9000"
            ).rstrip("/"),
            keycloak_admin_realm=_required_environment("KEYCLOAK_ADMIN_REALM", default="master"),
            clouddsp_realm=_required_environment("CLOUDDSP_REALM", default="clouddsp"),
            job_api_client_id=_required_environment("JOB_API_CLIENT_ID", default="clouddsp-job-api"),
            pod_name=_required_environment("POD_NAME"),
            keycloak_admin_username=_required_environment("KC_BOOTSTRAP_ADMIN_USERNAME"),
            keycloak_admin_password=_required_environment("KC_BOOTSTRAP_ADMIN_PASSWORD"),
            postgresql_host=_required_environment(
                "POSTGRESQL_HOST", default="clouddsp-postgresql.clouddsp-data.svc"
            ),
            postgresql_port=_bounded_integer(
                name="POSTGRESQL_PORT",
                value=os.environ.get("POSTGRESQL_PORT", "5432"),
                minimum=1,
                maximum=65_535,
            ),
            postgresql_database=_required_environment(
                "POSTGRESQL_JOB_DATABASE", default="clouddsp_job_api"
            ),
            postgresql_admin_username=_required_environment("POSTGRESQL_ADMIN_USERNAME"),
            postgresql_admin_password=_required_environment("POSTGRESQL_ADMIN_PASSWORD"),
            minio_root_username=_required_environment("MINIO_ROOT_USER"),
            minio_root_password=_required_environment("MINIO_ROOT_PASSWORD"),
            uploads_bucket=_required_environment("UPLOADS_BUCKET", default=LOCAL_UPLOADS_BUCKET),
            timeout_seconds=_bounded_integer(
                name="DISPATCHER_SMOKE_TIMEOUT_SECONDS",
                value=os.environ.get("DISPATCHER_SMOKE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)),
                minimum=1,
                maximum=MAX_TIMEOUT_SECONDS,
            ),
        )
        if settings.uploads_bucket != LOCAL_UPLOADS_BUCKET:
            raise DispatcherSmokeConfigurationError("Dispatcher smoke uploads bucket is not approved.")
        for setting_name, url in (
            ("KEYCLOAK_INTERNAL_BASE_URL", settings.keycloak_internal_base_url),
            ("JOB_API_INTERNAL_BASE_URL", settings.job_api_internal_base_url),
            ("MINIO_INTERNAL_BASE_URL", settings.minio_internal_base_url),
        ):
            parsed = urlsplit(url)
            if parsed.scheme != "http" or not parsed.hostname or parsed.query or parsed.fragment:
                raise DispatcherSmokeConfigurationError(f"{setting_name} must be a private HTTP Service URL.")
        return settings

    def database_settings(self) -> DispatcherSmokeDatabaseSettings:
        """Produce the read-only verifier settings from the test's admin route.

        PostgreSQL still enforces normal privileges.  The future Job mounts the
        existing local administrator Secret only because it must both inspect
        the event during setup and delete its exact temporary row during
        cleanup; the verifier itself uses no write statement.
        """

        return DispatcherSmokeDatabaseSettings(
            host=self.postgresql_host,
            port=self.postgresql_port,
            database=self.postgresql_database,
            username=self.postgresql_admin_username,
            password=self.postgresql_admin_password,
        )


@dataclass(frozen=True)
class ControlledUpload:
    """Private IDs produced by one normal Job API direct-upload contract."""

    job_id: str
    owner_sub: str
    object_key: str

    def __post_init__(self) -> None:
        """Validate scopes before cleanup can use an in-memory API response."""

        object.__setattr__(self, "job_id", _canonical_uuid(self.job_id, label="Job API job ID"))
        if not isinstance(self.owner_sub, str) or not self.owner_sub:
            raise DispatcherSmokeOrchestrationError("Keycloak temporary user ID was unavailable.")
        if not isinstance(self.object_key, str):
            raise DispatcherSmokeOrchestrationError("Job API upload key was unavailable.")
        prefix = f"uploads/{self.job_id}/"
        leaf = self.object_key[len(prefix) :] if self.object_key.startswith(prefix) else ""
        if not leaf or "/" in leaf:
            raise DispatcherSmokeOrchestrationError("Job API upload key was outside the temporary job scope.")


@dataclass
class TemporaryKeycloakResources:
    """Track generated identities as they are created so partial failures clean up.

    The normal-path setup contains several remote calls.  Recording each opaque
    ID immediately lets the outer ``finally`` remove a client/user even when a
    later request fails before a complete upload contract exists.
    """

    admin_token: str | None = field(default=None, repr=False)
    client_uuid: str | None = None
    user_id: str | None = None


def _http_request(
    name: str,
    url: str,
    expected_statuses: set[int],
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], bytes]:
    """Make one bounded HTTP request without logging URLs, headers, or bodies."""

    try:
        request = Request(url, data=data, headers={} if headers is None else dict(headers), method=method)
        with urlopen(request, timeout=10) as response:  # noqa: S310 - URLs come from reviewed settings/contracts.
            status = response.getcode()
            response_headers = dict(response.headers.items())
            response_body = response.read()
    except HTTPError as error:
        raise DispatcherSmokeOrchestrationError(f"{name} returned HTTP {error.code}.") from error
    except URLError as error:
        raise DispatcherSmokeOrchestrationError(f"{name} could not reach its private Service.") from error
    if status not in expected_statuses:
        raise DispatcherSmokeOrchestrationError(f"{name} returned unexpected HTTP {status}.")
    return response_headers, response_body


def _json_response(name: str, body: bytes) -> Mapping[str, Any]:
    """Parse one expected object response without exposing upstream content."""

    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise DispatcherSmokeOrchestrationError(f"{name} returned invalid JSON.") from error
    if not isinstance(value, Mapping):
        raise DispatcherSmokeOrchestrationError(f"{name} returned an unexpected JSON shape.")
    return value


def _json_request(
    name: str,
    url: str,
    expected_statuses: set[int],
    *,
    method: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
) -> tuple[dict[str, str], Mapping[str, Any]]:
    """Send one JSON request and retain its parsed response only in memory."""

    response_headers, body = _http_request(
        name,
        url,
        expected_statuses,
        method=method,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )
    return response_headers, _json_response(name, body) if body else {}


def _location_identifier(name: str, headers: Mapping[str, str], path_segment: str) -> str:
    """Obtain Keycloak's opaque created-resource ID without printing it."""

    location = headers.get("Location")
    marker = f"/{path_segment}/"
    if not isinstance(location, str) or marker not in location:
        raise DispatcherSmokeOrchestrationError(f"{name} omitted its created-resource location.")
    identifier = location.rstrip("/").rsplit("/", 1)[-1]
    if not identifier:
        raise DispatcherSmokeOrchestrationError(f"{name} returned an empty created-resource identifier.")
    return identifier


def _multipart_upload_body(fields: Mapping[str, Any], file_bytes: bytes) -> tuple[bytes, str]:
    """Build a browser-equivalent POST form entirely in temporary process memory."""

    boundary = f"----CloudDSPDispatcherSmoke{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise DispatcherSmokeOrchestrationError("Job API upload form contained invalid field text.")
        chunks.extend(
            (
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    chunks.extend(
        (
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="file"; filename="dispatcher-smoke.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        )
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def private_minio_upload_url(upload_url: object, minio_internal_base_url: str) -> str:
    """Replace only a signed browser URL's origin with in-cluster MinIO DNS.

    The Job API intentionally returns ``minio.localhost:8080`` because the
    browser needs Traefik's host route.  A Pod cannot use that host meaningfully
    and must not regenerate or edit the signature/form.  Presigned POST has no
    signature in the URL, so preserving its exact S3 path while using MinIO's
    private endpoint is safe and proves the API-issued form is authoritative.
    """

    if not isinstance(upload_url, str):
        raise DispatcherSmokeOrchestrationError("Job API upload contract was incomplete.")
    parsed = urlsplit(upload_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "minio.localhost"
        or parsed.port != 8080
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise DispatcherSmokeOrchestrationError("Job API upload contract used an unexpected browser MinIO route.")
    endpoint = urlsplit(minio_internal_base_url)
    if endpoint.scheme != "http" or not endpoint.netloc or endpoint.path not in ("", "/"):
        raise DispatcherSmokeConfigurationError("MINIO_INTERNAL_BASE_URL must be a plain private Service URL.")
    return minio_internal_base_url.rstrip("/") + parsed.path


def _tiny_wav_bytes() -> bytes:
    """Return a syntactically valid zero-frame WAV so no DSP workload starts."""

    return (
        b"RIFF"
        + struct.pack("<I", 36)
        + b"WAVEfmt "
        + struct.pack("<I", 16)
        + struct.pack("<HHIIHH", 1, 1, 8000, 8000, 1, 8)
        + b"data"
        + struct.pack("<I", 0)
    )


def _database_connection(settings: DispatcherSmokeOrchestratorSettings) -> Any:
    """Open the test's short administrator connection for lookup/cleanup only."""

    try:
        import psycopg
    except ImportError as error:
        raise DispatcherSmokeOrchestrationError("Pinned PostgreSQL smoke dependency is unavailable.") from error
    try:
        return psycopg.connect(
            host=settings.postgresql_host,
            port=settings.postgresql_port,
            dbname=settings.postgresql_database,
            user=settings.postgresql_admin_username,
            password=settings.postgresql_admin_password,
            connect_timeout=5,
            options="-c statement_timeout=5000",
        )
    except Exception as error:
        raise DispatcherSmokeOrchestrationError("PostgreSQL smoke connection is unavailable.") from error


SELECT_CONTROLLED_EVENT_SQL = """
    SELECT event_id::text, publication_status
    FROM public.outbox_events
    WHERE job_id = %s::uuid
      AND stage = 'demucs'
      AND stem_name = ''
      AND event_type = 'demucs.requested'
    ORDER BY created_at ASC
"""


def expected_event_from_outbox_rows(
    rows: object,
    upload: ControlledUpload,
    *,
    stem_mode: str = CONTROLLED_STEM_MODE,
) -> ExpectedDemucsRequested:
    """Turn exactly one durable controlled event into the verifier contract.

    ``pending``, ``leased``, and ``published`` are all acceptable here: the
    dispatcher is intentionally concurrent with this smoke setup.  The
    dedicated verifier subsequently waits until the event is fully published
    before it touches the queue.
    """

    if not isinstance(rows, list):
        raise DispatcherSmokeOrchestrationError("PostgreSQL returned an unexpected controlled-event collection.")
    if not rows:
        raise ControlledEventNotReady("Controlled Demucs event is not visible yet.")
    if len(rows) != 1:
        raise DispatcherSmokeOrchestrationError("PostgreSQL did not retain exactly one controlled Demucs event.")
    row = rows[0]
    if not isinstance(row, tuple) or len(row) != 2:
        raise DispatcherSmokeOrchestrationError("PostgreSQL returned an unexpected controlled-event shape.")
    event_id, publication_status = row
    if publication_status not in {"pending", "leased", "published"}:
        raise DispatcherSmokeOrchestrationError("Controlled Demucs event had an invalid delivery state.")
    return ExpectedDemucsRequested(
        event_id=_canonical_uuid(event_id, label="Outbox event ID"),
        job_id=upload.job_id,
        object_key=upload.object_key,
        stem_mode=stem_mode,
    )


def _read_controlled_event(settings: DispatcherSmokeOrchestratorSettings, upload: ControlledUpload) -> ExpectedDemucsRequested:
    """Read identifiers/state only; the private outbox payload is never selected."""

    with _database_connection(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SELECT_CONTROLLED_EVENT_SQL, (upload.job_id,))
            rows = cursor.fetchall()
    return expected_event_from_outbox_rows(rows, upload)


def wait_for_controlled_event(
    settings: DispatcherSmokeOrchestratorSettings,
    upload: ControlledUpload,
    *,
    read_event: Callable[[DispatcherSmokeOrchestratorSettings, ControlledUpload], ExpectedDemucsRequested] = _read_controlled_event,
    sleep_function: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> ExpectedDemucsRequested:
    """Wait for intake's atomic source transition and outbox insert to be visible."""

    deadline = monotonic() + settings.timeout_seconds
    last_not_ready: ControlledEventNotReady | None = None
    while monotonic() < deadline:
        try:
            return read_event(settings, upload)
        except ControlledEventNotReady as error:
            # A missing row is normal during the short MinIO/RabbitMQ/intake
            # handoff.  A future test can still report one safe final category.
            last_not_ready = error
            sleep_function(1)
    if last_not_ready is not None:
        raise DispatcherSmokeOrchestrationError(
            "upload-intake did not create the controlled Demucs event before the smoke timeout."
        ) from last_not_ready
    raise DispatcherSmokeOrchestrationError("Controlled Demucs event lookup did not start.")


def _wait_for_source_uploaded(
    settings: DispatcherSmokeOrchestratorSettings,
    access_token: str,
    job_id: str,
    *,
    sleep_function: Callable[[float], None] = time.sleep,
) -> None:
    """Poll the owner-bound API until normal intake commits the source state."""

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    job_url = f"{settings.job_api_internal_base_url}/jobs/{quote(job_id, safe='')}"
    for _ in range(settings.timeout_seconds):
        _, body = _http_request("Job API source-state poll", job_url, {200}, headers=headers)
        if _json_response("Job API source-state poll", body).get("status") == "source_uploaded":
            return
        sleep_function(1)
    raise DispatcherSmokeOrchestrationError("upload-intake did not reach source_uploaded before the smoke timeout.")


def _create_controlled_upload(
    settings: DispatcherSmokeOrchestratorSettings,
    temporary: TemporaryKeycloakResources,
) -> ControlledUpload:
    """Use normal Keycloak + Job API routes to create/upload one disposable job.

    ``temporary`` is filled after each created remote resource.  It exists so
    the outer ``finally`` can remove a partial Keycloak client/user if a later
    normal API request fails.  None of its values are logged.
    """

    pod_suffix = settings.pod_name.rsplit("-", 1)[-1]
    if not pod_suffix:
        raise DispatcherSmokeConfigurationError("Kubernetes did not provide a usable Pod suffix.")
    client_id = f"clouddsp-dispatcher-smoke-{pod_suffix}"
    username = f"dispatcher-smoke-{pod_suffix}"
    password = secrets.token_urlsafe(32)
    realm_path = quote(settings.clouddsp_realm, safe="")
    admin_realm_path = quote(settings.keycloak_admin_realm, safe="")

    _, token_body = _http_request(
        "Keycloak administrator token request",
        f"{settings.keycloak_internal_base_url}/realms/{admin_realm_path}/protocol/openid-connect/token",
        {200},
        method="POST",
        data=urlencode(
            {
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": settings.keycloak_admin_username,
                "password": settings.keycloak_admin_password,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temporary.admin_token = _json_response("Keycloak administrator token request", token_body).get("access_token")
    if not isinstance(temporary.admin_token, str) or not temporary.admin_token:
        raise DispatcherSmokeOrchestrationError("Keycloak administrator token response was incomplete.")
    admin_headers = {"Authorization": f"Bearer {temporary.admin_token}"}

    client_headers, _ = _json_request(
        "Keycloak temporary-client creation",
        f"{settings.keycloak_internal_base_url}/admin/realms/{realm_path}/clients",
        {201},
        method="POST",
        payload={
            "clientId": client_id,
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": True,
            "standardFlowEnabled": False,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": False,
            "fullScopeAllowed": False,
        },
        headers=admin_headers,
    )
    temporary.client_uuid = _location_identifier("Keycloak temporary-client creation", client_headers, "clients")
    _json_request(
        "Keycloak temporary-client audience mapper creation",
        (
            f"{settings.keycloak_internal_base_url}/admin/realms/{realm_path}/clients/"
            f"{quote(temporary.client_uuid, safe='')}/protocol-mappers/models"
        ),
        {201},
        method="POST",
        payload={
            "name": "job-api-access-token-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "consentRequired": False,
            "config": {
                "included.client.audience": settings.job_api_client_id,
                "access.token.claim": "true",
                "id.token.claim": "false",
                "introspection.token.claim": "true",
                "userinfo.token.claim": "false",
            },
        },
        headers=admin_headers,
    )
    user_headers, _ = _json_request(
        "Keycloak temporary-user creation",
        f"{settings.keycloak_internal_base_url}/admin/realms/{realm_path}/users",
        {201},
        method="POST",
        payload={
            "username": username,
            "email": f"{username}@clouddsp.test",
            "firstName": "CloudDSP",
            "lastName": "Dispatcher Smoke",
            "enabled": True,
            "emailVerified": True,
        },
        headers=admin_headers,
    )
    temporary.user_id = _location_identifier("Keycloak temporary-user creation", user_headers, "users")
    _json_request(
        "Keycloak temporary-user password setup",
        f"{settings.keycloak_internal_base_url}/admin/realms/{realm_path}/users/{quote(temporary.user_id, safe='')}/reset-password",
        {204},
        method="PUT",
        payload={"type": "password", "temporary": False, "value": password},
        headers=admin_headers,
    )
    _, user_token_body = _http_request(
        "Keycloak temporary-user token request",
        f"{settings.keycloak_internal_base_url}/realms/{realm_path}/protocol/openid-connect/token",
        {200},
        method="POST",
        data=urlencode(
            {
                "grant_type": "password",
                "client_id": client_id,
                "username": username,
                "password": password,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_token = _json_response("Keycloak temporary-user token request", user_token_body).get("access_token")
    if not isinstance(user_token, str) or not user_token:
        raise DispatcherSmokeOrchestrationError("Keycloak temporary-user token response was incomplete.")

    wav_bytes = _tiny_wav_bytes()
    _, create_body = _http_request(
        "Job API direct-upload creation",
        f"{settings.job_api_internal_base_url}/jobs",
        {201},
        method="POST",
        data=json.dumps(
            {
                "filename": "dispatcher-smoke.wav",
                "content_type": "audio/wav",
                "size_bytes": len(wav_bytes),
                "stem_mode": CONTROLLED_STEM_MODE,
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
    )
    contract = _json_response("Job API direct-upload creation", create_body)
    if contract.get("status") != "upload_pending":
        raise DispatcherSmokeOrchestrationError("Job API direct-upload creation returned an invalid job contract.")
    fields = contract.get("upload_fields")
    if not isinstance(fields, Mapping):
        raise DispatcherSmokeOrchestrationError("Job API direct-upload creation omitted its upload form.")
    upload = ControlledUpload(
        job_id=_canonical_uuid(contract.get("job_id"), label="Job API job ID"),
        owner_sub=temporary.user_id,
        object_key=fields.get("key"),
    )
    upload_body, content_type = _multipart_upload_body(fields, wav_bytes)
    _http_request(
        "MinIO presigned source upload",
        private_minio_upload_url(contract.get("upload_url"), settings.minio_internal_base_url),
        {204},
        method="POST",
        data=upload_body,
        headers={"Content-Type": content_type, "Content-Length": str(len(upload_body))},
    )
    _wait_for_source_uploaded(settings, user_token, upload.job_id)
    return upload


def _delete_temporary_object(settings: DispatcherSmokeOrchestratorSettings, upload: ControlledUpload) -> None:
    """Delete only the object created by this Job before deleting its DB row."""

    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise DispatcherSmokeOrchestrationError("Pinned MinIO smoke dependency is unavailable.") from error
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.minio_internal_base_url,
            aws_access_key_id=settings.minio_root_username,
            aws_secret_access_key=settings.minio_root_password,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        s3.delete_object(Bucket=settings.uploads_bucket, Key=upload.object_key)
    except Exception as error:
        raise DispatcherSmokeOrchestrationError("MinIO temporary source-object cleanup failed.") from error


def _delete_temporary_job(settings: DispatcherSmokeOrchestratorSettings, upload: ControlledUpload) -> None:
    """Delete exactly the test-owned row; its foreign keys cascade the outbox row."""

    with _database_connection(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.jobs WHERE job_id = %s::uuid AND owner_sub = %s",
                (upload.job_id, upload.owner_sub),
            )
            if cursor.rowcount != 1:
                raise DispatcherSmokeOrchestrationError(
                    "PostgreSQL temporary job cleanup did not delete exactly one row."
                )


def _delete_keycloak_resource(
    settings: DispatcherSmokeOrchestratorSettings,
    admin_token: str,
    *,
    kind: str,
    identifier: str,
) -> None:
    """Delete a generated Keycloak client or user through the private admin API."""

    if kind not in {"clients", "users"}:
        raise DispatcherSmokeConfigurationError("Temporary Keycloak cleanup kind is not approved.")
    _http_request(
        f"Keycloak temporary-{kind[:-1]} cleanup",
        (
            f"{settings.keycloak_internal_base_url}/admin/realms/"
            f"{quote(settings.clouddsp_realm, safe='')}/{kind}/{quote(identifier, safe='')}"
        ),
        {204},
        method="DELETE",
        headers={"Authorization": f"Bearer {admin_token}"},
    )


def run_orchestrated_dispatcher_smoke(settings: DispatcherSmokeOrchestratorSettings) -> None:
    """Perform the bounded normal-path setup, verification, and scoped cleanup.

    Queue preflight comes first: once it declares that the Demucs queue is
    empty, this run creates one source.  The verifier may then consume exactly
    that resulting message.  If any step fails, cleanup still targets only the
    generated IDs and does not drain/reorder arbitrary broker traffic.
    """

    amqp_settings = DispatcherSmokeAmqpSettings.from_environment()
    run_preflight(amqp_settings)
    temporary = TemporaryKeycloakResources()
    upload: ControlledUpload | None = None
    primary_failure: Exception | None = None
    cleanup_failed = False
    try:
        print("Dispatcher smoke: creating authenticated normal direct upload")
        upload = _create_controlled_upload(settings, temporary)
        print("Dispatcher smoke: waiting for the controlled outbox event")
        expected = wait_for_controlled_event(settings, upload)
        print("Dispatcher smoke: verifying durable publication and AMQP delivery")
        run_verification(
            amqp_settings=amqp_settings,
            database_settings=settings.database_settings(),
            expected=expected,
            timeout_seconds=settings.timeout_seconds,
        )
    except Exception as error:  # Store, then clean up without exposing raw library diagnostics.
        primary_failure = error
    finally:
        if upload is not None:
            try:
                _delete_temporary_object(settings, upload)
                _delete_temporary_job(settings, upload)
            except Exception:
                cleanup_failed = True
        if temporary.admin_token and temporary.user_id:
            try:
                _delete_keycloak_resource(
                    settings, temporary.admin_token, kind="users", identifier=temporary.user_id
                )
            except Exception:
                cleanup_failed = True
        elif temporary.user_id:
            cleanup_failed = True
        if temporary.admin_token and temporary.client_uuid:
            try:
                _delete_keycloak_resource(
                    settings, temporary.admin_token, kind="clients", identifier=temporary.client_uuid
                )
            except Exception:
                cleanup_failed = True
        elif temporary.client_uuid:
            cleanup_failed = True
    if cleanup_failed:
        raise DispatcherSmokeOrchestrationError("Dispatcher smoke cleanup did not complete.")
    if primary_failure is not None:
        if isinstance(
            primary_failure,
            (
                DispatcherSmokeConfigurationError,
                DispatcherSmokeOrchestrationError,
                DispatcherSmokeAssertionError,
                DispatcherSmokeInfrastructureError,
            ),
        ):
            raise primary_failure
        # AMQP/SQL verifier exceptions already have fixed safe messages, but
        # keep unknown lower-level driver text from a user-visible Job log.
        raise DispatcherSmokeOrchestrationError(
            f"Dispatcher smoke verification failed with {type(primary_failure).__name__}."
        ) from primary_failure


def main() -> int:
    """Run the future disposable Job without serializing any sensitive contract."""

    try:
        run_orchestrated_dispatcher_smoke(DispatcherSmokeOrchestratorSettings.from_environment())
    except (
        DispatcherSmokeConfigurationError,
        DispatcherSmokeOrchestrationError,
        DispatcherSmokeAssertionError,
        DispatcherSmokeInfrastructureError,
    ) as error:
        print(f"Dispatcher smoke test failed: {error}", file=sys.stderr)
        return 1
    print("Dispatcher smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
