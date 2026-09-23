"""Authenticated API and presigned-upload ingress for the six-stem load test.

This module is deliberately the load test's *user-equivalent ingress* only:

``temporary Keycloak user -> POST /jobs -> constrained MinIO POST``

It has no Keycloak administrator credential, PostgreSQL/MinIO/RabbitMQ
application credential, Kubernetes API token, queue operation, data cleanup,
or processing-status assertion. The suspended load-test Job composes these
durable Job coordinates with separately scoped observer containers. Keeping
ingress separate means the test cannot pass by inserting rows or AMQP messages
directly.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import sys
from typing import Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit
import wave
from uuid import UUID

from keycloak_identity_lifecycle import (
    HttpResponse,
    KeycloakTransport,
    UrllibKeycloakTransport,
)
from lifecycle_handoff import (
    AuthenticatedLoadCoordinates,
    HandoffError,
    SubmittedLoadJobCoordinate,
    TemporaryLoadClientIdentity,
    wait_for_temporary_load_identity,
    write_authenticated_load_coordinates,
    write_client_outcome,
)


_JOB_COUNT = 3
_STEM_MODE = "6-stems"
_SOURCE_CONTENT_TYPE = "audio/wav"
_SOURCE_SAMPLE_RATE = 44_100
_SOURCE_DURATION_SECONDS = 2
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_HTTP_JSON_BYTES = 64 * 1024
_EXPECTED_KEYCLOAK_INTERNAL_BASE_URL = "http://clouddsp-keycloak.clouddsp-data.svc:8080"
_EXPECTED_JOB_API_INTERNAL_BASE_URL = "http://clouddsp-job-api.clouddsp-app.svc:80"
_EXPECTED_MINIO_INTERNAL_BASE_URL = "http://clouddsp-minio.clouddsp-data.svc:9000"
_EXPECTED_MINIO_BROWSER_HOST = "minio.localhost"
_EXPECTED_MINIO_BROWSER_PORT = 8080
_FORM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AuthenticatedLoadClientError(RuntimeError):
    """A safe client failure that never includes credentials, URLs, or response bodies."""


@dataclass(frozen=True)
class AuthenticatedLoadClientSettings:
    """The three fixed in-cluster destinations needed by authenticated ingress."""

    keycloak_internal_base_url: str
    application_realm: str
    job_api_internal_base_url: str
    minio_internal_base_url: str
    handoff_directory: str

    @classmethod
    def from_environment(cls) -> "AuthenticatedLoadClientSettings":
        """Read only reviewed private Service DNS values from the future Pod environment."""

        keycloak_url = _required_environment(
            "KEYCLOAK_INTERNAL_BASE_URL", default=_EXPECTED_KEYCLOAK_INTERNAL_BASE_URL
        ).rstrip("/")
        job_api_url = _required_environment(
            "JOB_API_INTERNAL_BASE_URL", default=_EXPECTED_JOB_API_INTERNAL_BASE_URL
        ).rstrip("/")
        minio_url = _required_environment(
            "MINIO_INTERNAL_BASE_URL", default=_EXPECTED_MINIO_INTERNAL_BASE_URL
        ).rstrip("/")
        if keycloak_url != _EXPECTED_KEYCLOAK_INTERNAL_BASE_URL:
            raise AuthenticatedLoadClientError("Keycloak endpoint must be the reviewed private Service")
        if job_api_url != _EXPECTED_JOB_API_INTERNAL_BASE_URL:
            raise AuthenticatedLoadClientError("Job API endpoint must be the reviewed private Service")
        if minio_url != _EXPECTED_MINIO_INTERNAL_BASE_URL:
            raise AuthenticatedLoadClientError("MinIO endpoint must be the reviewed private Service")
        realm = _required_environment("CLOUDDSP_REALM", default="clouddsp")
        if realm != "clouddsp" or "/" in realm or "\x00" in realm:
            raise AuthenticatedLoadClientError("application realm must be the reviewed CloudDSP realm")
        return cls(
            keycloak_internal_base_url=keycloak_url,
            application_realm=realm,
            job_api_internal_base_url=job_api_url,
            minio_internal_base_url=minio_url,
            handoff_directory=_required_environment("SIX_STEM_LOAD_HANDOFF_DIR"),
        )


@dataclass(frozen=True)
class AuthenticatedIngressResult:
    """The complete private result of the normal three-job ingress boundary.

    The broker treats ``coordinates`` as an untrusted claim and verifies it
    through normal owner-bound Job API reads before minting any
    observer capability. Its subject, Job IDs, and checksums are durable-work
    identifiers, so this result must never be written to ordinary Pod logs.
    """

    coordinates: AuthenticatedLoadCoordinates = field(repr=False)


def submit_three_concurrent_six_stem_uploads(
    *,
    settings: AuthenticatedLoadClientSettings,
    identity: TemporaryLoadClientIdentity,
    transport: KeycloakTransport | None = None,
) -> AuthenticatedIngressResult:
    """Use the normal API/form path for exactly three concurrent six-stem jobs.

    The temporary password is exchanged for one short-lived access token in
    memory.  Three independent worker threads each create a normal durable Job
    and post one generated WAV through that Job's returned form.  A thread
    failure never causes a fourth submission, direct database write, queue
    publish, or cleanup attempt; later diagnosis retains the partial evidence.
    """

    client = transport or UrllibKeycloakTransport()
    # These fixed milestones identify the failing boundary without logging
    # tokens, URLs, response bodies, Job IDs, or user-controlled values.
    print("Six-stem authenticated client: requesting temporary-user token", flush=True)
    token = request_temporary_user_access_token(
        settings=settings,
        identity=identity,
        transport=client,
    )
    print("Six-stem authenticated client: temporary-user token received", flush=True)
    print("Six-stem authenticated client: verifying Job API identity", flush=True)
    subject = _verify_authenticated_subject(
        settings=settings, access_token=token, transport=client
    )
    print("Six-stem authenticated client: Job API identity verified", flush=True)

    # Submit all three initial requests together.  This is the approved
    # bounded concurrency point—not a production benchmark or unbounded queue
    # producer. Keeping one thread per Job makes each POST/form transfer
    # independent while sharing the same authenticated user identity.
    futures: dict[Future[SubmittedLoadJobCoordinate], int] = {}
    with ThreadPoolExecutor(max_workers=_JOB_COUNT, thread_name_prefix="six-stem-load") as executor:
        for ordinal in range(1, _JOB_COUNT + 1):
            futures[
                executor.submit(
                    _create_and_upload_one,
                    ordinal=ordinal,
                    settings=settings,
                    identity=identity,
                    access_token=token,
                    transport=client,
                )
            ] = ordinal

        results: list[SubmittedLoadJobCoordinate | None] = [None] * _JOB_COUNT
        failure_seen = False
        for future, ordinal in futures.items():
            try:
                results[ordinal - 1] = future.result()
            except Exception as error:
                failure_seen = True
                # Every helper emits one safe error category. Future/HTTP
                # libraries can carry URLs or headers in arbitrary exceptions,
                # so retain only the public category here.
                if isinstance(error, AuthenticatedLoadClientError):
                    continue
        if failure_seen:
            raise AuthenticatedLoadClientError("one or more six-stem direct uploads failed")

    if any(result is None for result in results):
        raise AuthenticatedLoadClientError("six-stem direct upload result set was incomplete")
    completed = tuple(result for result in results if result is not None)
    coordinates = AuthenticatedLoadCoordinates(
        run_marker=identity.run_marker,
        subject=subject,
        stem_mode=_STEM_MODE,
        jobs=completed,
    )
    _validate_submitted_set(identity=identity, coordinates=coordinates)
    return AuthenticatedIngressResult(coordinates=coordinates)


def submit_and_write_authenticated_load_coordinates(
    *,
    settings: AuthenticatedLoadClientSettings,
    identity: TemporaryLoadClientIdentity,
    handoff_directory: Path,
    transport: KeycloakTransport | None = None,
) -> AuthenticatedIngressResult:
    """Complete ingress, then atomically pass only its coordinates to the broker.

    No coordinate file is created unless all three signed uploads complete.
    This helper deliberately does **not** write the pre-existing terminal
    client-outcome marker: the lifecycle broker first verifies these untrusted
    coordinates and provisions restricted
    observers before declaring the whole load run successful or failed.
    """

    result = submit_three_concurrent_six_stem_uploads(
        settings=settings, identity=identity, transport=transport
    )
    write_authenticated_load_coordinates(handoff_directory, coordinates=result.coordinates)
    return result


def request_temporary_user_access_token(
    *,
    settings: AuthenticatedLoadClientSettings,
    identity: TemporaryLoadClientIdentity,
    transport: KeycloakTransport,
) -> str:
    """Exchange only the broker-handoff user credentials for one short-lived JWT."""

    response = transport.request(
        operation="six-stem-load temporary-user token request",
        url=(
            f"{settings.keycloak_internal_base_url}/realms/{quote(settings.application_realm, safe='')}"
            "/protocol/openid-connect/token"
        ),
        expected_statuses=frozenset({200}),
        method="POST",
        data=urlencode(
            {
                "grant_type": "password",
                "client_id": identity.client_id,
                "username": identity.username,
                "password": identity.password,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    payload = _json_object(operation="temporary-user token request", body=response.body)
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise AuthenticatedLoadClientError("temporary-user token response was incomplete")
    return token


def build_controlled_load_wav(*, ordinal: int) -> bytes:
    """Build one valid, deterministic two-second stereo WAV entirely in memory.

    The distinct frequency pair gives the three upload artifacts different
    bytes without introducing copyrighted media or a runtime audio dependency.
    Two seconds is long enough for the existing real Demucs smoke path while
    keeping the three incoming transfers negligible beside CPU separation.
    """

    if ordinal not in range(1, _JOB_COUNT + 1):
        raise ValueError("ordinal must select one of the three approved load sources")
    left_frequency = 180 + 30 * ordinal
    right_frequency = 330 + 40 * ordinal
    frames = bytearray()
    for sample_index in range(_SOURCE_SAMPLE_RATE * _SOURCE_DURATION_SECONDS):
        left = int(0.22 * 32767 * math.sin(2 * math.pi * left_frequency * sample_index / _SOURCE_SAMPLE_RATE))
        right = int(0.18 * 32767 * math.sin(2 * math.pi * right_frequency * sample_index / _SOURCE_SAMPLE_RATE))
        frames.extend(struct.pack("<hh", left, right))
    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(_SOURCE_SAMPLE_RATE)
        writer.writeframes(bytes(frames))
    source = destination.getvalue()
    if not 44 <= len(source) <= _MAX_SOURCE_BYTES or source[:4] != b"RIFF" or source[8:12] != b"WAVE":
        raise AuthenticatedLoadClientError("controlled load WAV construction was invalid")
    return source


def multipart_upload_body(
    *,
    fields: Mapping[str, str],
    source_filename: str,
    source_bytes: bytes,
) -> tuple[bytes, str]:
    """Build the exact browser-style multipart POST body for a signed MinIO form."""

    if not fields:
        raise AuthenticatedLoadClientError("direct-upload form contained no fields")
    if not source_filename.endswith(".wav") or "/" in source_filename or "\\" in source_filename:
        raise AuthenticatedLoadClientError("controlled source filename was invalid")
    boundary = f"----CloudDSPSixStemLoad{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or name == "file"
            or not _FORM_NAME_PATTERN.fullmatch(name)
            or "\r" in value
            or "\n" in value
        ):
            raise AuthenticatedLoadClientError("direct-upload form contained invalid text")
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
            f'Content-Disposition: form-data; name="file"; filename="{source_filename}"\r\n'.encode("utf-8"),
            b"Content-Type: audio/wav\r\n\r\n",
            source_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        )
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _create_and_upload_one(
    *,
    ordinal: int,
    settings: AuthenticatedLoadClientSettings,
    identity: TemporaryLoadClientIdentity,
    access_token: str,
    transport: KeycloakTransport,
) -> SubmittedLoadJobCoordinate:
    """Create and upload one Job, preserving its server-generated ownership boundary."""

    source_filename = f"six-stem-load-{identity.run_marker}-{ordinal}.wav"
    source_bytes = build_controlled_load_wav(ordinal=ordinal)
    print(f"Six-stem authenticated client: creating Job {ordinal} via API", flush=True)
    try:
        contract = _create_direct_upload_contract(
            ordinal=ordinal,
            settings=settings,
            access_token=access_token,
            source_filename=source_filename,
            source_size_bytes=len(source_bytes),
            transport=transport,
        )
    except Exception:
        # Use a fixed phase label. Library exceptions can retain response
        # bodies, signed URLs, or headers, so none are copied into Pod logs.
        print(
            f"Six-stem authenticated client: Job {ordinal} API creation failed",
            file=sys.stderr,
            flush=True,
        )
        raise
    print(f"Six-stem authenticated client: Job {ordinal} API contract accepted", flush=True)
    try:
        _upload_through_internal_minio(
            ordinal=ordinal,
            settings=settings,
            source_filename=source_filename,
            source_bytes=source_bytes,
            contract=contract,
            transport=transport,
        )
    except Exception:
        print(
            f"Six-stem authenticated client: Job {ordinal} MinIO upload failed",
            file=sys.stderr,
            flush=True,
        )
        raise
    print(f"Six-stem authenticated client: Job {ordinal} MinIO upload completed", flush=True)
    return SubmittedLoadJobCoordinate(
        ordinal=ordinal,
        job_id=contract["job_id"],
        source_filename=source_filename,
        source_size_bytes=len(source_bytes),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )


def _verify_authenticated_subject(
    *, settings: AuthenticatedLoadClientSettings, access_token: str, transport: KeycloakTransport
) -> str:
    """Prove the normal Job API accepted the signed user token before creating state."""

    response = transport.request(
        operation="six-stem-load authenticated identity request",
        url=f"{settings.job_api_internal_base_url}/auth/me",
        expected_statuses=frozenset({200}),
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    payload = _json_object(operation="authenticated identity request", body=response.body)
    if set(payload) != {"subject"} or not isinstance(payload.get("subject"), str):
        raise AuthenticatedLoadClientError("authenticated identity response was invalid")
    try:
        canonical_subject = str(UUID(payload["subject"]))
    except (ValueError, AttributeError) as error:
        raise AuthenticatedLoadClientError("authenticated identity response had an invalid subject") from error
    if canonical_subject != payload["subject"]:
        raise AuthenticatedLoadClientError("authenticated identity response had a noncanonical subject")
    return canonical_subject


def _create_direct_upload_contract(
    *,
    ordinal: int,
    settings: AuthenticatedLoadClientSettings,
    access_token: str,
    source_filename: str,
    source_size_bytes: int,
    transport: KeycloakTransport,
) -> dict[str, object]:
    """Call the ordinary owner-bound POST /jobs route and validate its signed form contract."""

    response = transport.request(
        operation=f"six-stem-load direct-upload Job creation {ordinal}",
        url=f"{settings.job_api_internal_base_url}/jobs",
        expected_statuses=frozenset({201}),
        method="POST",
        data=json.dumps(
            {
                "filename": source_filename,
                "content_type": _SOURCE_CONTENT_TYPE,
                "size_bytes": source_size_bytes,
                "stem_mode": _STEM_MODE,
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    payload = _json_object(operation=f"direct-upload Job creation {ordinal}", body=response.body)
    job_id = _canonical_uuid(payload.get("job_id"))
    fields = payload.get("upload_fields")
    revision = payload.get("revision")
    if (
        payload.get("status") != "upload_pending"
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or not isinstance(payload.get("upload_url"), str)
        or not isinstance(fields, dict)
        or not all(isinstance(name, str) and isinstance(value, str) for name, value in fields.items())
    ):
        raise AuthenticatedLoadClientError("direct-upload Job contract was incomplete")
    expected_key = f"uploads/{job_id}/{source_filename}"
    required_fields = {
        "key": expected_key,
        "Content-Type": _SOURCE_CONTENT_TYPE,
        "x-amz-meta-job-id": job_id,
        "x-amz-meta-stem-mode": _STEM_MODE,
    }
    if any(fields.get(name) != value for name, value in required_fields.items()):
        raise AuthenticatedLoadClientError("direct-upload Job contract did not bind the expected source")
    return {"job_id": job_id, "upload_url": payload["upload_url"], "upload_fields": fields}


def _upload_through_internal_minio(
    *,
    ordinal: int,
    settings: AuthenticatedLoadClientSettings,
    source_filename: str,
    source_bytes: bytes,
    contract: Mapping[str, object],
    transport: KeycloakTransport,
) -> None:
    """Preserve the API-issued form but route this in-cluster Pod through MinIO Service DNS."""

    upload_url = contract.get("upload_url")
    fields = contract.get("upload_fields")
    if not isinstance(upload_url, str) or not isinstance(fields, dict):
        raise AuthenticatedLoadClientError("direct-upload contract was incomplete before upload")
    parsed = urlsplit(upload_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != _EXPECTED_MINIO_BROWSER_HOST
        or parsed.port != _EXPECTED_MINIO_BROWSER_PORT
        # A form generated for a browser-visible origin must have no user-info.
        # The test does not reuse that origin directly, but accepting user-info
        # here would make its contract review needlessly ambiguous.
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise AuthenticatedLoadClientError("direct-upload contract used an unexpected browser MinIO origin")
    typed_fields = {name: value for name, value in fields.items() if isinstance(name, str) and isinstance(value, str)}
    if len(typed_fields) != len(fields):
        raise AuthenticatedLoadClientError("direct-upload form had a non-text field")
    body, content_type = multipart_upload_body(
        fields=typed_fields,
        source_filename=source_filename,
        source_bytes=source_bytes,
    )
    transport.request(
        operation=f"six-stem-load presigned source upload {ordinal}",
        url=f"{settings.minio_internal_base_url}{parsed.path}",
        expected_statuses=frozenset({204}),
        method="POST",
        data=body,
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
    )


def _validate_submitted_set(
    *, identity: TemporaryLoadClientIdentity, coordinates: AuthenticatedLoadCoordinates
) -> None:
    """Refuse an incomplete/duplicated run before it reaches the broker handoff."""

    submissions: Sequence[SubmittedLoadJobCoordinate] = coordinates.jobs
    if coordinates.run_marker != identity.run_marker or coordinates.stem_mode != _STEM_MODE:
        raise AuthenticatedLoadClientError("six-stem submission metadata was invalid")
    if len(submissions) != _JOB_COUNT:
        raise AuthenticatedLoadClientError("six-stem submission count was invalid")
    if {submission.ordinal for submission in submissions} != {1, 2, 3}:
        raise AuthenticatedLoadClientError("six-stem submission ordinals were invalid")
    if len({submission.job_id for submission in submissions}) != _JOB_COUNT:
        raise AuthenticatedLoadClientError("six-stem Job API returned duplicate identifiers")
    expected_filenames = {
        f"six-stem-load-{identity.run_marker}-{ordinal}.wav" for ordinal in range(1, _JOB_COUNT + 1)
    }
    if {submission.source_filename for submission in submissions} != expected_filenames:
        raise AuthenticatedLoadClientError("six-stem submission filenames were invalid")


def _canonical_uuid(value: object) -> str:
    """Require the API's lowercase canonical UUID rather than accepting arbitrary text."""

    if not isinstance(value, str):
        raise AuthenticatedLoadClientError("direct-upload Job contract omitted its Job identifier")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise AuthenticatedLoadClientError("direct-upload Job contract had an invalid Job identifier") from error
    if canonical != value:
        raise AuthenticatedLoadClientError("direct-upload Job contract had a noncanonical Job identifier")
    return canonical


def _json_object(*, operation: str, body: bytes) -> dict[str, object]:
    """Parse a bounded JSON response without carrying its contents into an exception."""

    if len(body) > _MAX_HTTP_JSON_BYTES:
        raise AuthenticatedLoadClientError(f"{operation} response exceeded 64 KiB")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthenticatedLoadClientError(f"{operation} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise AuthenticatedLoadClientError(f"{operation} returned an invalid JSON shape")
    return payload


def _required_environment(name: str, *, default: str | None = None) -> str:
    """Read a nonempty setting without putting its value into an error message."""

    value = os.environ.get(name, default)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AuthenticatedLoadClientError(f"required load-client setting {name} was absent")
    return value


def main() -> int:
    """Run only the authenticated API and browser-equivalent upload contract.

    Kubernetes starts ordinary containers concurrently, not in a user-chosen
    order. This process therefore waits for the broker's 0600 temporary-user
    handoff, submits three concurrent six-stem Jobs through the real Job API,
    and publishes an all-or-nothing coordinate file only after each presigned
    upload succeeds. A minimal failure marker lets the broker clean up early;
    neither file can create observer authority.
    """

    # This path is also pinned in the suspended Job manifest. A fallback lets
    # the failure marker wake the broker if an env typo affects other settings.
    handoff_directory = Path(
        os.environ.get("SIX_STEM_LOAD_HANDOFF_DIR", "/var/run/clouddsp-six-stem-load")
    )
    try:
        settings = AuthenticatedLoadClientSettings.from_environment()
        print("Six-stem authenticated client: waiting for temporary-user handoff")
        identity = wait_for_temporary_load_identity(handoff_directory)
        print("Six-stem authenticated client: creating and uploading three six-stem jobs")
        result = submit_and_write_authenticated_load_coordinates(
            settings=settings,
            identity=identity,
            handoff_directory=handoff_directory,
        )
        # The coordinate file is the durable handoff; this extra marker is
        # only an early-failure signal to the broker and is never success proof.
        write_client_outcome(handoff_directory, outcome="succeeded")
        if len(result.coordinates.jobs) != _JOB_COUNT:
            raise AuthenticatedLoadClientError("six-stem upload count was invalid")
        print("Six-stem authenticated client: three uploads completed")
        return 0
    except Exception:
        try:
            write_client_outcome(handoff_directory, outcome="failed")
        except (HandoffError, OSError, ValueError):
            # The main failure stays generic. If the tmpfs is unavailable the
            # broker still has its finite timeout and Kubernetes deadline.
            pass
        print("Six-stem authenticated client failed", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by the Job's client container.
    # The Job controller reads the process exit code, not a Python return
    # value. Propagate failure so a stopped client cannot appear Completed.
    raise SystemExit(main())
