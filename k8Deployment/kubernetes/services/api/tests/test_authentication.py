"""Focused tests for the Job API Keycloak access-token boundary.

These are regular-library ``unittest`` tests so they need no test-only package
inside the production image.  Run them after building the next image revision
in a Python environment that installed ``requirements.lock``.
"""

from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.authentication import (
    AuthenticatedPrincipal,
    AuthenticationUnavailable,
    InvalidAccessToken,
    KeycloakAccessTokenValidator,
    KeycloakTokenSettings,
    require_authenticated_principal,
)
from app.main import app, authenticated_identity_response


ISSUER = "http://keycloak.localhost:8080/realms/clouddsp"
AUDIENCE = "clouddsp-job-api"


def base64url_unsigned_integer(value: int) -> str:
    """Encode an RSA public-number integer in JWK's Base64URL form."""

    byte_length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


class KeycloakAccessTokenValidatorTests(unittest.TestCase):
    """Prove valid tokens work and cross-resource tokens are rejected."""

    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
        public_numbers = self.private_key.public_key().public_numbers()
        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key",
                    "use": "sig",
                    "alg": "RS256",
                    "n": base64url_unsigned_integer(public_numbers.n),
                    "e": base64url_unsigned_integer(public_numbers.e),
                }
            ]
        }
        self.settings = KeycloakTokenSettings(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="http://clouddsp-keycloak.clouddsp-data.svc:8080/realms/clouddsp/protocol/openid-connect/certs",
        )
        self.validator = KeycloakAccessTokenValidator(
            self.settings,
            # This dependency injection avoids network activity in unit tests
            # while exercising the exact JWK parsing used by the API process.
            jwks_fetcher=lambda **_kwargs: self.jwks,
        )

    def make_token(self, *, audience: str = AUDIENCE) -> str:
        """Create a short-lived test access token signed by the test RSA key."""

        now = int(time.time())
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": audience,
                "sub": "keycloak-user-immutable-subject",
                "iat": now,
                "exp": now + 60,
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_valid_access_token_returns_verified_subject(self) -> None:
        principal = self.validator.validate(self.make_token())
        self.assertEqual(principal.subject, "keycloak-user-immutable-subject")

    def test_token_for_another_resource_is_rejected(self) -> None:
        with self.assertRaises(InvalidAccessToken):
            self.validator.validate(self.make_token(audience="some-other-resource"))


class AuthenticationDependencyAndRouteTests(unittest.TestCase):
    """Prove the first protected route receives only a trusted principal."""

    def test_missing_bearer_token_returns_generic_401(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            require_authenticated_principal(None)

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Authentication is required.")
        self.assertEqual(raised.exception.headers["WWW-Authenticate"], 'Bearer realm="clouddsp"')

    def test_verified_bearer_token_returns_only_validator_subject(self) -> None:
        validator = Mock()
        validator.validate.return_value = AuthenticatedPrincipal(subject="verified-keycloak-subject")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-token")

        with patch("app.authentication.get_token_validator", return_value=validator):
            principal = require_authenticated_principal(credentials)

        self.assertEqual(principal.subject, "verified-keycloak-subject")
        validator.validate.assert_called_once_with("not-a-real-token")

    def test_keycloak_signing_key_outage_returns_generic_503(self) -> None:
        validator = Mock()
        validator.validate.side_effect = AuthenticationUnavailable("private test detail")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-token")

        with patch("app.authentication.get_token_validator", return_value=validator):
            with self.assertRaises(HTTPException) as raised:
                require_authenticated_principal(credentials)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Authentication service is temporarily unavailable.")

    def test_identity_response_contains_only_verified_subject(self) -> None:
        response = authenticated_identity_response(
            AuthenticatedPrincipal(subject="verified-keycloak-subject")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            json.loads(response.body),
            {"subject": "verified-keycloak-subject"},
        )

    def test_identity_route_is_registered_as_get(self) -> None:
        route = next(route for route in app.routes if getattr(route, "path", None) == "/auth/me")
        self.assertEqual(route.methods, {"GET"})
