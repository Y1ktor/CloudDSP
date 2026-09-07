"""Keycloak access-token validation for CloudDSP's local Job API.

The React application is a *public client*: it obtains an access token from
Keycloak but cannot be trusted to validate that token. This module is the
resource-server boundary powering `GET /auth/me`; every future `/jobs` route
will use the same ``require_authenticated_principal`` dependency before it
reads or writes PostgreSQL.

There are deliberately two Keycloak addresses in the settings below:

* ``issuer`` is the public, browser-facing address embedded in tokens.  It is
  checked exactly, preventing a token from another realm or issuer being used.
* ``jwks_url`` is the private Kubernetes Service address used only by this API
  Pod to obtain Keycloak's public signing keys.  It avoids making an in-cluster
  verification request depend on Traefik or the Mac's localhost routing.

No token, decoded claims, authorization header, or signing key is logged.  The
only value returned to a later route is the verified immutable ``sub`` claim.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


# Keycloak signs the locally-issued access tokens with RSA.  Supplying an
# explicit allow-list is essential: the JWT header is attacker-controlled and
# must never be allowed to choose an algorithm such as ``none`` or HS256.
ALLOWED_SIGNING_ALGORITHMS = ("RS256",)
DEFAULT_JWKS_TIMEOUT_SECONDS = 3
DEFAULT_JWKS_CACHE_SECONDS = 300
MAX_JWKS_TIMEOUT_SECONDS = 10
MAX_JWKS_CACHE_SECONDS = 3_600


class AuthenticationConfigurationError(RuntimeError):
    """Raised when the Deployment lacks a safe OIDC configuration value."""


class AuthenticationUnavailable(RuntimeError):
    """Raised when the API cannot obtain a usable public signing-key set."""


class InvalidAccessToken(RuntimeError):
    """Raised when a presented bearer token cannot be authenticated."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The small trusted identity shape available to future Job API routes.

    ``subject`` comes from Keycloak's signed ``sub`` claim.  It is stable for a
    realm user and therefore suitable for the ``jobs.owner_sub`` column.  A
    browser-supplied account identifier is never accepted as an alternative.
    """

    subject: str


def required_environment_value(name: str) -> str:
    """Read a required non-empty setting without logging its value."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AuthenticationConfigurationError(
            f"Required environment variable {name} is absent."
        )
    return value.strip()


def required_http_url(name: str) -> str:
    """Return an absolute HTTP(S) URL with no ambiguous trailing slash.

    The deployment uses HTTP for this intentional local-only cluster.  HTTPS
    is accepted too, so moving this same code to a TLS-enabled environment is
    a configuration change rather than an application rewrite.
    """

    value = required_environment_value(name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AuthenticationConfigurationError(
            f"{name} must be an absolute HTTP(S) URL."
        )
    if value.endswith("/"):
        raise AuthenticationConfigurationError(
            f"{name} must not end with a slash."
        )
    return value


def bounded_positive_integer(*, name: str, value: str, maximum: int) -> int:
    """Parse a small bounded timeout/cache setting from the environment."""

    try:
        parsed = int(value)
    except ValueError as error:
        raise AuthenticationConfigurationError(
            f"{name} must be an integer."
        ) from error
    if not 1 <= parsed <= maximum:
        raise AuthenticationConfigurationError(
            f"{name} must be between 1 and {maximum}."
        )
    return parsed


@dataclass(frozen=True)
class KeycloakTokenSettings:
    """Non-secret OIDC values supplied by the Job API Deployment.

    ``audience`` names the Job API resource client, not the React SPA client.
    A separate Keycloak configuration task will add that audience to access
    tokens issued for ``clouddsp-react`` before a browser route relies on it.
    """

    issuer: str
    audience: str
    jwks_url: str
    jwks_timeout_seconds: int = DEFAULT_JWKS_TIMEOUT_SECONDS
    jwks_cache_seconds: int = DEFAULT_JWKS_CACHE_SECONDS

    @classmethod
    def from_environment(cls) -> "KeycloakTokenSettings":
        """Build settings only when authentication is first required."""

        return cls(
            issuer=required_http_url("JOB_API_OIDC_ISSUER"),
            audience=required_environment_value("JOB_API_OIDC_AUDIENCE"),
            jwks_url=required_http_url("JOB_API_OIDC_JWKS_URL"),
            jwks_timeout_seconds=bounded_positive_integer(
                name="JOB_API_OIDC_JWKS_TIMEOUT_SECONDS",
                value=os.environ.get(
                    "JOB_API_OIDC_JWKS_TIMEOUT_SECONDS",
                    str(DEFAULT_JWKS_TIMEOUT_SECONDS),
                ),
                maximum=MAX_JWKS_TIMEOUT_SECONDS,
            ),
            jwks_cache_seconds=bounded_positive_integer(
                name="JOB_API_OIDC_JWKS_CACHE_SECONDS",
                value=os.environ.get(
                    "JOB_API_OIDC_JWKS_CACHE_SECONDS",
                    str(DEFAULT_JWKS_CACHE_SECONDS),
                ),
                maximum=MAX_JWKS_CACHE_SECONDS,
            ),
        )


def fetch_jwks(*, url: str, timeout_seconds: int) -> dict[str, Any]:
    """Fetch and parse Keycloak's public JSON Web Key Set over cluster DNS."""

    request = Request(
        url,
        headers={"Accept": "application/json"},
        # The request is a read of public Keycloak metadata.  It deliberately
        # sends no browser token, cookies, or Keycloak administrator credential.
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            # A successful TCP request with a redirect or an HTML error page is
            # not a valid key set.  Failing closed keeps a misrouted API from
            # accepting stale or unexpected authentication metadata.
            response_status = getattr(response, "status", 200)
            if response_status != 200:
                raise AuthenticationUnavailable("Keycloak JWKS returned a non-200 status.")
            document = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthenticationUnavailable("Keycloak JWKS is unavailable.") from error

    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise AuthenticationUnavailable("Keycloak JWKS has an invalid document shape.")
    return document


class KeycloakAccessTokenValidator:
    """Validate RS256 Keycloak access tokens using a short, thread-safe JWKS cache.

    A Pod fetches keys only when its cache expires, and once immediately when a
    token has a new ``kid`` after Keycloak signing-key rotation.  The lock means
    simultaneous HTTP requests do not all issue their own JWKS request.
    """

    def __init__(
        self,
        settings: KeycloakTokenSettings,
        *,
        jwks_fetcher: Callable[..., dict[str, Any]] = fetch_jwks,
    ) -> None:
        self._settings = settings
        self._jwks_fetcher = jwks_fetcher
        self._cached_keys: dict[str, jwt.PyJWK] = {}
        self._cache_expires_at = 0.0
        self._cache_lock = Lock()

    def _load_keys(self, *, force_refresh: bool = False) -> dict[str, jwt.PyJWK]:
        """Return usable RSA signing keys, refreshing one shared cache if needed."""

        with self._cache_lock:
            if not force_refresh and time.monotonic() < self._cache_expires_at:
                return self._cached_keys

            document = self._jwks_fetcher(
                url=self._settings.jwks_url,
                timeout_seconds=self._settings.jwks_timeout_seconds,
            )
            parsed_keys: dict[str, jwt.PyJWK] = {}
            for candidate in document["keys"]:
                # Ignore keys that cannot verify the one reviewed algorithm. A
                # Keycloak realm can publish encryption or future-algorithm keys
                # alongside RS256 signing keys without widening API trust.
                if not isinstance(candidate, dict):
                    continue
                key_id = candidate.get("kid")
                if (
                    not isinstance(key_id, str)
                    or not key_id
                    or candidate.get("kty") != "RSA"
                    or candidate.get("use", "sig") != "sig"
                    or candidate.get("alg") not in {None, "RS256"}
                    or key_id in parsed_keys
                ):
                    continue
                try:
                    parsed_keys[key_id] = jwt.PyJWK.from_dict(candidate, algorithm="RS256")
                except jwt.PyJWKError:
                    # One malformed public key must not make an unrelated valid
                    # signing key unusable. An empty usable set fails closed.
                    continue

            if not parsed_keys:
                raise AuthenticationUnavailable("Keycloak JWKS contains no usable RS256 signing key.")

            self._cached_keys = parsed_keys
            self._cache_expires_at = time.monotonic() + self._settings.jwks_cache_seconds
            return self._cached_keys

    def _signing_key_for(self, key_id: str) -> jwt.PyJWK:
        """Find a token's signing key and retry once after planned key rotation."""

        keys = self._load_keys()
        signing_key = keys.get(key_id)
        if signing_key is not None:
            return signing_key

        # An unknown ``kid`` is expected briefly during a Keycloak rotation.
        # Force exactly one refresh; repeated unknown keys remain a 401 instead
        # of making every malformed token generate unbounded metadata traffic.
        signing_key = self._load_keys(force_refresh=True).get(key_id)
        if signing_key is None:
            raise InvalidAccessToken("The token signing key is unknown.")
        return signing_key

    def validate(self, token: str) -> AuthenticatedPrincipal:
        """Verify one compact JWT and return its trusted immutable subject.

        PyJWT checks the signature plus ``exp``, ``nbf``, and ``iat`` semantics.
        Supplying the expected issuer and audience makes an otherwise valid JWT
        from another realm or for another resource fail authorization here.
        """

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in ALLOWED_SIGNING_ALGORITHMS:
                raise InvalidAccessToken("The token uses an unsupported algorithm.")
            key_id = header.get("kid")
            if not isinstance(key_id, str) or not key_id:
                raise InvalidAccessToken("The token has no signing-key identifier.")

            claims = jwt.decode(
                token,
                key=self._signing_key_for(key_id).key,
                algorithms=list(ALLOWED_SIGNING_ALGORITHMS),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except AuthenticationUnavailable:
            # Keycloak infrastructure failure is distinct from an invalid user
            # token. The HTTP dependency below maps it to a retryable 503.
            raise
        except (jwt.InvalidTokenError, TypeError, ValueError) as error:
            raise InvalidAccessToken("The bearer token is invalid.") from error

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidAccessToken("The bearer token has no usable subject.")
        return AuthenticatedPrincipal(subject=subject)


# ``auto_error=False`` lets this module return one deliberate RFC 6750-style
# response for a missing header, a non-Bearer header, or a failed validation.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="KeycloakBearer")
logger = logging.getLogger(__name__)


def unauthorized_response() -> HTTPException:
    """Create the intentionally generic 401 response for an invalid token."""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
        headers={"WWW-Authenticate": 'Bearer realm="clouddsp"'},
    )


@lru_cache(maxsize=1)
def get_token_validator() -> KeycloakAccessTokenValidator:
    """Create one cache-owning validator per long-lived API process."""

    return KeycloakAccessTokenValidator(KeycloakTokenSettings.from_environment())


def require_authenticated_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    """FastAPI dependency future business routes use to obtain an owner identity.

    `GET /auth/me` uses this dependency as the narrow live proof that an API
    request receives a Keycloak-verified immutable subject. A later `/jobs`
    task adds it to every user-owned route instead of repeating JWT parsing in
    route handlers.
    """

    if credentials is None:
        raise unauthorized_response()

    try:
        return get_token_validator().validate(credentials.credentials)
    except InvalidAccessToken:
        raise unauthorized_response() from None
    except (AuthenticationConfigurationError, AuthenticationUnavailable):
        # Keep failures observable in server logs without emitting a token,
        # claim, Keycloak URL, or driver exception in a browser response.
        logger.warning("Job API authentication dependency is unavailable.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
            headers={"Cache-Control": "no-store"},
        ) from None
