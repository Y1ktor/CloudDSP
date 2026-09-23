"""Verify the load client's coordinates through the ordinary owner-bound API.

The authenticated ingress client writes a private coordinate file, but that
file remains an untrusted inter-container claim. This adapter is the broker's
next boundary: it obtains a *fresh* token for the temporary user and proves
each of the three server-generated Job IDs is visible through the normal
``GET /jobs/{job_id}`` route for that same subject.

It has no PostgreSQL, MinIO, RabbitMQ, Kubernetes, or Keycloak-admin API
operation. In particular, successful verification is not an observer
capability grant and does not mean processing completed. A later broker task
will use this bounded evidence before minting independently restricted
observer identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Mapping
from urllib.parse import quote, urlencode
from uuid import UUID

from keycloak_identity_lifecycle import HttpResponse, KeycloakTransport, LoadTestIdentity, UrllibKeycloakTransport
from lifecycle_handoff import AuthenticatedLoadCoordinates, SubmittedLoadJobCoordinate


_MAX_HTTP_JSON_BYTES = 64 * 1024
_EXPECTED_KEYCLOAK_INTERNAL_BASE_URL = "http://clouddsp-keycloak.clouddsp-data.svc:8080"
_EXPECTED_JOB_API_INTERNAL_BASE_URL = "http://clouddsp-job-api.clouddsp-app.svc:80"
_EXPECTED_APPLICATION_REALM = "clouddsp"
_EXPECTED_STEM_MODE = "6-stems"
_EXPECTED_SOURCE_CONTENT_TYPE = "audio/wav"


class OwnerBoundJobVerificationError(RuntimeError):
    """A safe failure category that excludes credentials, IDs, URLs, and bodies."""


@dataclass(frozen=True)
class OwnerBoundJobVerificationSettings:
    """The two reviewed private Services needed for owner-bound verification."""

    keycloak_internal_base_url: str
    application_realm: str
    job_api_internal_base_url: str

    def __post_init__(self) -> None:
        """Reject endpoint substitution before a temporary user token is requested."""

        if self.keycloak_internal_base_url.rstrip("/") != _EXPECTED_KEYCLOAK_INTERNAL_BASE_URL:
            raise OwnerBoundJobVerificationError("Keycloak endpoint must be the reviewed private Service")
        if self.job_api_internal_base_url.rstrip("/") != _EXPECTED_JOB_API_INTERNAL_BASE_URL:
            raise OwnerBoundJobVerificationError("Job API endpoint must be the reviewed private Service")
        if self.application_realm != _EXPECTED_APPLICATION_REALM:
            raise OwnerBoundJobVerificationError("application realm must be the reviewed CloudDSP realm")


@dataclass(frozen=True)
class VerifiedOwnerBoundLoadJobs:
    """Validated three-job evidence that is safe only as input to later broker logic.

    The identifiers have no dataclass representation because normal Pod logs
    do not need durable user-work coordinates. This proof grants no database,
    object-store, queue, or Kubernetes authority by itself.
    """

    subject: str = field(repr=False)
    coordinates: AuthenticatedLoadCoordinates = field(repr=False)


def verify_owner_bound_load_jobs(
    *,
    settings: OwnerBoundJobVerificationSettings,
    identity: LoadTestIdentity,
    coordinates: AuthenticatedLoadCoordinates,
    transport: KeycloakTransport | None = None,
) -> VerifiedOwnerBoundLoadJobs:
    """Verify every handoff coordinate through one temporary user's normal API view.

    The coordinate-file parser already enforces its three-entry syntax. This
    function binds that claim to the exact Keycloak identity created for the
    same marker, rechecks the API's immutable subject, and then performs three
    `GET /jobs/{job_id}` calls. It deliberately never lists Jobs, so an API
    response cannot silently substitute a different retained workload.
    """

    _validate_identity_matches_coordinates(identity=identity, coordinates=coordinates)
    client = transport or UrllibKeycloakTransport()
    access_token = _temporary_user_access_token(
        settings=settings, identity=identity, transport=client
    )
    subject = _verify_authenticated_subject(
        settings=settings, access_token=access_token, transport=client
    )
    if subject != coordinates.subject:
        raise OwnerBoundJobVerificationError("temporary user did not match the coordinate owner")

    for job in coordinates.jobs:
        _verify_one_owner_bound_job(
            settings=settings,
            access_token=access_token,
            coordinate=job,
            transport=client,
        )
    return VerifiedOwnerBoundLoadJobs(subject=subject, coordinates=coordinates)


def _validate_identity_matches_coordinates(
    *, identity: LoadTestIdentity, coordinates: AuthenticatedLoadCoordinates
) -> None:
    """Bind the broker's in-memory identity to its private client-returned marker."""

    if identity.run_marker != coordinates.run_marker:
        raise OwnerBoundJobVerificationError("temporary identity did not match the coordinate run")
    # The trusted lifecycle adapter issued the client ID. This narrow shape
    # check prevents a future caller from accidentally passing a browser client
    # while retaining the random password in memory.
    expected_client_id = f"clouddsp-six-stem-load-{coordinates.run_marker}"
    if identity.client_id != expected_client_id:
        raise OwnerBoundJobVerificationError("temporary identity did not match the reviewed load client")


def _temporary_user_access_token(
    *,
    settings: OwnerBoundJobVerificationSettings,
    identity: LoadTestIdentity,
    transport: KeycloakTransport,
) -> str:
    """Request one fresh direct-grant token with no administrator credential."""

    response = transport.request(
        operation="six-stem broker temporary-user token request",
        url=(
            f"{settings.keycloak_internal_base_url.rstrip('/')}/realms/"
            f"{quote(settings.application_realm, safe='')}/protocol/openid-connect/token"
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
    payload = _json_object(operation="broker temporary-user token request", body=response.body)
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise OwnerBoundJobVerificationError("broker temporary-user token response was incomplete")
    return token


def _verify_authenticated_subject(
    *,
    settings: OwnerBoundJobVerificationSettings,
    access_token: str,
    transport: KeycloakTransport,
) -> str:
    """Ask the Job API which immutable subject its token validator accepted."""

    response = transport.request(
        operation="six-stem broker authenticated identity request",
        url=f"{settings.job_api_internal_base_url.rstrip('/')}/auth/me",
        expected_statuses=frozenset({200}),
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    payload = _json_object(operation="broker authenticated identity request", body=response.body)
    if set(payload) != {"subject"}:
        raise OwnerBoundJobVerificationError("broker authenticated identity response was invalid")
    return _canonical_uuid(payload.get("subject"), purpose="broker authenticated identity subject")


def _verify_one_owner_bound_job(
    *,
    settings: OwnerBoundJobVerificationSettings,
    access_token: str,
    coordinate: SubmittedLoadJobCoordinate,
    transport: KeycloakTransport,
) -> None:
    """Require one API snapshot to exactly reproduce one coordinate's safe fields."""

    response = transport.request(
        operation=f"six-stem broker owner-bound Job verification {coordinate.ordinal}",
        url=(
            f"{settings.job_api_internal_base_url.rstrip('/')}/jobs/"
            f"{quote(coordinate.job_id, safe='')}"
        ),
        expected_statuses=frozenset({200}),
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    payload = _json_object(
        operation=f"owner-bound Job verification {coordinate.ordinal}", body=response.body
    )
    if (
        _canonical_uuid(payload.get("job_id"), purpose="owner-bound Job identifier") != coordinate.job_id
        or payload.get("source_type") != "direct_upload"
        or payload.get("source_filename") != coordinate.source_filename
        or payload.get("source_content_type") != _EXPECTED_SOURCE_CONTENT_TYPE
        or payload.get("source_size_bytes") != coordinate.source_size_bytes
        or payload.get("stem_mode") != _EXPECTED_STEM_MODE
        or not isinstance(payload.get("source_uploaded"), bool)
        or isinstance(payload.get("revision"), bool)
        or not isinstance(payload.get("revision"), int)
        or payload["revision"] < 1
        or not isinstance(payload.get("status"), str)
        or not payload["status"]
        or not isinstance(payload.get("stems"), dict)
        or not isinstance(payload.get("midi"), dict)
    ):
        raise OwnerBoundJobVerificationError("owner-bound Job did not match the coordinate contract")


def _json_object(*, operation: str, body: bytes) -> dict[str, object]:
    """Parse one bounded response without carrying a service body into errors."""

    if len(body) > _MAX_HTTP_JSON_BYTES:
        raise OwnerBoundJobVerificationError(f"{operation} response exceeded 64 KiB")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnerBoundJobVerificationError(f"{operation} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise OwnerBoundJobVerificationError(f"{operation} returned an invalid JSON shape")
    return payload


def _canonical_uuid(value: object, *, purpose: str) -> str:
    """Require canonical UUID text while keeping received values out of errors."""

    if not isinstance(value, str):
        raise OwnerBoundJobVerificationError(f"{purpose} was invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise OwnerBoundJobVerificationError(f"{purpose} was invalid") from error
    if canonical != value:
        raise OwnerBoundJobVerificationError(f"{purpose} was invalid")
    return canonical
