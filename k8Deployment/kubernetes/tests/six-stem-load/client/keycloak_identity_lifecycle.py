"""Create and revoke the disposable Keycloak identity used by a load run.

This module intentionally models only the identity boundary of the future
three-job six-stem load test.  It is not the React OIDC implementation, does
not automate a browser, and does not create a Kubernetes resource.  The later
load-test lifecycle broker will call it from a dedicated container that alone
mounts the Keycloak bootstrap-administrator Secret.

The authenticated load-client container receives the returned *temporary user*
configuration through a bounded shared volume.  It never receives the
administrator credentials or access token held by this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import secrets
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


# A Kubernetes Job-generated suffix is short, lowercase, and alphanumeric.
# Requiring a slightly longer explicit marker gives an interrupted load run a
# safe, human-searchable correlation value without allowing a caller to insert
# a path separator, whitespace, Keycloak query syntax, or arbitrary prefix.
_RUN_MARKER_PATTERN = re.compile(r"^[a-z0-9]{8,24}$")
_MAX_RESPONSE_BYTES = 64 * 1024
_DIRECT_GRANT_CLIENT_PREFIX = "clouddsp-six-stem-load-"
_TEMPORARY_USER_PREFIX = "six-stem-load-"
_TEMPORARY_EMAIL_DOMAIN = "clouddsp.test"


class IdentityLifecycleError(RuntimeError):
    """A safe failure category that never carries credentials or response data."""


@dataclass(frozen=True)
class HttpResponse:
    """The bounded data an HTTP implementation returns to this adapter."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class KeycloakTransport(Protocol):
    """Small injectable HTTP boundary used by both runtime code and unit tests."""

    def request(
        self,
        *,
        operation: str,
        url: str,
        expected_statuses: frozenset[int],
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        """Perform one bounded request or raise ``IdentityLifecycleError``."""


class UrllibKeycloakTransport:
    """Standard-library Keycloak HTTP client with no third-party dependency."""

    def request(
        self,
        *,
        operation: str,
        url: str,
        expected_statuses: frozenset[int],
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        """Send one request while withholding URLs, bodies, and credentials from errors."""

        try:
            with urlopen(
                Request(url, data=data, headers=dict(headers or {}), method=method),
                timeout=10,
            ) as response:
                status = response.status
                # Keycloak resource responses are small.  Reading one byte over
                # the cap detects a misrouted/unbounded endpoint without storing
                # its whole response in the load Job process.
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                response_headers = dict(response.headers.items())
        except HTTPError as error:
            raise IdentityLifecycleError(f"{operation} returned HTTP {error.code}") from None
        except (URLError, OSError, TimeoutError) as error:
            raise IdentityLifecycleError(f"{operation} failed: {type(error).__name__}") from None

        if status not in expected_statuses:
            raise IdentityLifecycleError(f"{operation} returned HTTP {status}")
        if len(body) > _MAX_RESPONSE_BYTES:
            raise IdentityLifecycleError(f"{operation} response exceeded 64 KiB")
        return HttpResponse(status=status, headers=response_headers, body=body)


@dataclass(frozen=True)
class LoadTestIdentity:
    """Opaque Keycloak identifiers and temporary direct-grant user credentials.

    ``password`` deliberately has no dataclass representation.  Callers must
    keep this object in process memory or serialize only the required fields to
    a mode-restricted, short-lived ``emptyDir`` handoff; it must never be logged
    or written to a committed Kubernetes Secret.
    """

    run_marker: str
    client_id: str
    client_uuid: str
    user_id: str
    username: str
    email: str
    password: str = field(repr=False)


class KeycloakLoadIdentityLifecycle:
    """Provision and revoke one isolated direct-grant client/user pair.

    The permanent React OIDC client remains Authorization Code + PKCE only.
    This adapter creates a separate public client with Standard and implicit
    flows disabled, Direct Access Grants enabled, no service account, no role
    mappings, and only the Job API audience mapper required by the local API.
    """

    def __init__(
        self,
        *,
        keycloak_internal_base_url: str,
        administrator_realm: str,
        application_realm: str,
        job_api_client_id: str,
        bootstrap_admin_username: str,
        bootstrap_admin_password: str,
        transport: KeycloakTransport | None = None,
    ) -> None:
        self._base_url = _validated_base_url(keycloak_internal_base_url)
        self._administrator_realm = _validated_realm_name(administrator_realm)
        self._application_realm = _validated_realm_name(application_realm)
        self._job_api_client_id = _validated_client_id(job_api_client_id)
        self._bootstrap_admin_username = _required_secret_value(
            "bootstrap administrator username", bootstrap_admin_username
        )
        self._bootstrap_admin_password = _required_secret_value(
            "bootstrap administrator password", bootstrap_admin_password
        )
        self._transport: KeycloakTransport = transport or UrllibKeycloakTransport()

    def create(self, *, run_marker: str) -> LoadTestIdentity:
        """Create one public direct-grant client, user, password, and audience mapper.

        A partially created identity is immediately cleaned up with the same
        in-memory admin token.  The caller gets no object until all four admin
        operations have succeeded, so it cannot accidentally start load work
        using a client without the required Job API audience.
        """

        marker = _validated_run_marker(run_marker)
        client_id = f"{_DIRECT_GRANT_CLIENT_PREFIX}{marker}"
        username = f"{_TEMPORARY_USER_PREFIX}{marker}"
        email = f"{username}@{_TEMPORARY_EMAIL_DOMAIN}"
        password = secrets.token_urlsafe(32)
        admin_token: str | None = None
        client_uuid: str | None = None
        user_id: str | None = None

        try:
            admin_token = self._administrator_access_token()
            admin_headers = self._admin_headers(admin_token)

            client_response = self._json_request(
                operation="Keycloak six-stem-load client creation",
                url=self._admin_realm_url("clients"),
                expected_statuses=frozenset({201}),
                method="POST",
                payload={
                    "clientId": client_id,
                    "name": "CloudDSP disposable six-stem load client",
                    "description": "Removed by the local six-stem load lifecycle broker.",
                    "enabled": True,
                    "protocol": "openid-connect",
                    "publicClient": True,
                    "standardFlowEnabled": False,
                    "implicitFlowEnabled": False,
                    "directAccessGrantsEnabled": True,
                    "serviceAccountsEnabled": False,
                    "authorizationServicesEnabled": False,
                    "fullScopeAllowed": False,
                    # A direct-grant-only test client is never a browser
                    # redirect target.  Explicitly empty values prevent a
                    # Keycloak default from widening this client later.
                    "redirectUris": [],
                    "webOrigins": [],
                },
                headers=admin_headers,
            )
            client_uuid = _location_identifier(
                operation="Keycloak six-stem-load client creation",
                headers=client_response.headers,
                resource_segment="clients",
            )

            self._json_request(
                operation="Keycloak six-stem-load Job API audience mapper creation",
                url=self._admin_realm_url(
                    f"clients/{quote(client_uuid, safe='')}/protocol-mappers/models"
                ),
                expected_statuses=frozenset({201}),
                method="POST",
                payload={
                    "name": "job-api-access-token-audience",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-audience-mapper",
                    "consentRequired": False,
                    "config": {
                        "included.client.audience": self._job_api_client_id,
                        "access.token.claim": "true",
                        "id.token.claim": "false",
                        "introspection.token.claim": "true",
                        "userinfo.token.claim": "false",
                    },
                },
                headers=admin_headers,
            )

            user_response = self._json_request(
                operation="Keycloak six-stem-load user creation",
                url=self._admin_realm_url("users"),
                expected_statuses=frozenset({201}),
                method="POST",
                payload={
                    "username": username,
                    "email": email,
                    "firstName": "CloudDSP",
                    "lastName": "Six Stem Load",
                    "enabled": True,
                    # Test users never receive email.  Marking the disposable
                    # address verified prevents the local realm's normal
                    # verification action from interrupting the password flow.
                    "emailVerified": True,
                },
                headers=admin_headers,
            )
            user_id = _location_identifier(
                operation="Keycloak six-stem-load user creation",
                headers=user_response.headers,
                resource_segment="users",
            )

            self._json_request(
                operation="Keycloak six-stem-load user password setup",
                url=self._admin_realm_url(
                    f"users/{quote(user_id, safe='')}/reset-password"
                ),
                expected_statuses=frozenset({204}),
                method="PUT",
                payload={"type": "password", "temporary": False, "value": password},
                headers=admin_headers,
            )
        except Exception as error:
            cleanup_ok = self._cleanup_created_resources(
                admin_token=admin_token,
                user_id=user_id,
                client_uuid=client_uuid,
            )
            if not cleanup_ok:
                raise IdentityLifecycleError(
                    "Keycloak six-stem-load identity provisioning and cleanup failed"
                ) from None
            if isinstance(error, IdentityLifecycleError):
                raise
            # Do not preserve an unexpected exception message: a mocked or
            # future HTTP library might include request headers in it.
            raise IdentityLifecycleError("Keycloak six-stem-load identity provisioning failed") from None

        # The preceding operations either produced both Keycloak resource IDs
        # or raised.  Keep the check explicit so a future refactor fails closed.
        if client_uuid is None or user_id is None:
            raise IdentityLifecycleError("Keycloak six-stem-load identity provisioning was incomplete")
        return LoadTestIdentity(
            run_marker=marker,
            client_id=client_id,
            client_uuid=client_uuid,
            user_id=user_id,
            username=username,
            email=email,
            password=password,
        )

    def request_user_access_token(self, *, identity: LoadTestIdentity) -> str:
        """Return one short-lived temporary-user token without logging or decoding it."""

        _validated_run_marker(identity.run_marker)
        token_response = self._transport.request(
            operation="Keycloak six-stem-load temporary-user token request",
            url=self._realm_url("protocol/openid-connect/token"),
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
        parsed = _bounded_json_object(
            operation="Keycloak six-stem-load temporary-user token request",
            body=token_response.body,
        )
        access_token = parsed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise IdentityLifecycleError("Keycloak six-stem-load token response was incomplete")
        return access_token

    def revoke(self, *, identity: LoadTestIdentity) -> None:
        """Delete the temporary user first, then its direct-grant client.

        This operation obtains a fresh administrator token because a real load
        run can outlive the token used during provisioning.  It attempts both
        deletions even when the first one fails, then reports one safe error.
        """

        _validated_run_marker(identity.run_marker)
        _validated_keycloak_uuid("temporary client", identity.client_uuid)
        _validated_keycloak_uuid("temporary user", identity.user_id)
        admin_token = self._administrator_access_token()
        if not self._cleanup_created_resources(
            admin_token=admin_token,
            user_id=identity.user_id,
            client_uuid=identity.client_uuid,
        ):
            raise IdentityLifecycleError("Keycloak six-stem-load identity cleanup failed")

    def _administrator_access_token(self) -> str:
        """Exchange ignored bootstrap credentials for one short-lived Admin API token."""

        response = self._transport.request(
            operation="Keycloak bootstrap administrator token request",
            url=(
                f"{self._base_url}/realms/{quote(self._administrator_realm, safe='')}"
                "/protocol/openid-connect/token"
            ),
            expected_statuses=frozenset({200}),
            method="POST",
            data=urlencode(
                {
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": self._bootstrap_admin_username,
                    "password": self._bootstrap_admin_password,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        parsed = _bounded_json_object(
            operation="Keycloak bootstrap administrator token request", body=response.body
        )
        token = parsed.get("access_token")
        if not isinstance(token, str) or not token:
            raise IdentityLifecycleError("Keycloak bootstrap administrator token response was incomplete")
        return token

    def _cleanup_created_resources(
        self,
        *,
        admin_token: str | None,
        user_id: str | None,
        client_uuid: str | None,
    ) -> bool:
        """Best-effort resource cleanup that never leaks an HTTP body in a failure."""

        if not admin_token:
            return user_id is None and client_uuid is None
        headers = self._admin_headers(admin_token)
        successful = True
        for operation, resource_segment, resource_id in (
            ("Keycloak six-stem-load temporary-user cleanup", "users", user_id),
            ("Keycloak six-stem-load temporary-client cleanup", "clients", client_uuid),
        ):
            if resource_id is None:
                continue
            try:
                _validated_keycloak_uuid(resource_segment, resource_id)
                self._transport.request(
                    operation=operation,
                    url=self._admin_realm_url(
                        f"{resource_segment}/{quote(resource_id, safe='')}"
                    ),
                    expected_statuses=frozenset({204}),
                    method="DELETE",
                    headers=headers,
                )
            except Exception:
                # Continue to the second resource so a failed user deletion
                # does not unnecessarily leave a still-usable direct-grant
                # client behind.  The public caller sees one safe failure.
                successful = False
        return successful

    def _json_request(
        self,
        *,
        operation: str,
        url: str,
        expected_statuses: frozenset[int],
        method: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        """Encode JSON centrally so every Admin API write has the same boundary."""

        request_headers = {**headers, "Content-Type": "application/json"}
        return self._transport.request(
            operation=operation,
            url=url,
            expected_statuses=expected_statuses,
            method=method,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
        )

    def _admin_headers(self, token: str) -> Mapping[str, str]:
        """Build an in-memory bearer header without ever returning the token to callers."""

        return {"Authorization": f"Bearer {token}"}

    def _admin_realm_url(self, path: str) -> str:
        """Return one internal Keycloak Admin API endpoint for the application realm."""

        return f"{self._base_url}/admin/realms/{quote(self._application_realm, safe='')}/{path}"

    def _realm_url(self, path: str) -> str:
        """Return one internal OIDC endpoint for the application realm."""

        return f"{self._base_url}/realms/{quote(self._application_realm, safe='')}/{path}"


def _validated_base_url(value: str) -> str:
    """Accept one absolute internal HTTP(S) endpoint without user-info/query fragments."""

    if not isinstance(value, str) or not value:
        raise ValueError("keycloak_internal_base_url must be one non-empty URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("keycloak_internal_base_url must be an absolute HTTP(S) service URL")
    return value.rstrip("/")


def _validated_realm_name(value: str) -> str:
    """Keep a realm name to one URL path segment before it enters an Admin URL."""

    if not isinstance(value, str) or not value or "/" in value or "\x00" in value:
        raise ValueError("Keycloak realm name must be one non-empty path segment")
    return value


def _validated_client_id(value: str) -> str:
    """Keep the audience mapper's configured client ID a short opaque identifier."""

    if not isinstance(value, str) or not value or len(value) > 255 or "\x00" in value:
        raise ValueError("Job API client ID must be one non-empty value up to 255 characters")
    return value


def _required_secret_value(name: str, value: str) -> str:
    """Reject absent Secret-derived configuration without embedding it in diagnostics."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _validated_run_marker(value: str) -> str:
    """Keep the load-run marker safe in usernames, emails, and source filenames."""

    if not isinstance(value, str) or not _RUN_MARKER_PATTERN.fullmatch(value):
        raise ValueError("run_marker must be 8-24 lowercase alphanumeric characters")
    return value


def _validated_keycloak_uuid(label: str, value: str) -> None:
    """Fail closed if a Location header was not the opaque Keycloak UUID expected."""

    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise IdentityLifecycleError(f"Keycloak {label} identifier was invalid") from error


def _location_identifier(
    *, operation: str, headers: Mapping[str, str], resource_segment: str
) -> str:
    """Extract and validate one final UUID from a Keycloak create Location header."""

    location = headers.get("Location")
    marker = f"/{resource_segment}/"
    if not isinstance(location, str) or marker not in location:
        raise IdentityLifecycleError(f"{operation} omitted its location")
    identifier = location.rstrip("/").rsplit("/", 1)[-1]
    _validated_keycloak_uuid(resource_segment, identifier)
    return identifier


def _bounded_json_object(*, operation: str, body: bytes) -> Mapping[str, object]:
    """Decode a small expected JSON object without retaining response text in errors."""

    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityLifecycleError(f"{operation} returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise IdentityLifecycleError(f"{operation} returned an invalid JSON shape")
    return parsed
