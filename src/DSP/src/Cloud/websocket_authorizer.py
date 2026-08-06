"""Authorize CloudDSP WebSocket connections with a Cognito ID token.

Browser WebSockets cannot send an arbitrary ``Authorization`` header during
the opening handshake, so the client supplies the short-lived ID token as the
``token`` query parameter over WSS.  This handler never logs that token.  It
downloads Cognito's public signing keys, validates issuer, audience, expiry,
and ``token_use``, then returns the Cognito subject as API Gateway's
``principalId``.
"""

import os
from typing import Any

import jwt
from jwt import InvalidTokenError, PyJWKClient


ISSUER = os.environ["COGNITO_USER_POOL_ISSUER"]
AUDIENCE = os.environ["COGNITO_BROWSER_CLIENT_ID"]
JWKS_CLIENT = PyJWKClient(
    f"{ISSUER}/.well-known/jwks.json",
    cache_keys=True,
    max_cached_keys=16,
    cache_jwk_set=True,
    lifespan=3600,
)


def deny(message: str) -> Exception:
    """Return an exception that API Gateway maps to an unauthorized connect."""
    return Exception(f"Unauthorized: {message}")


def token_from_event(event: dict[str, Any]) -> str:
    parameters = event.get("queryStringParameters") or {}
    token = parameters.get("token")
    if not isinstance(token, str) or not token:
        raise deny("missing token")
    return token


def validated_subject(token: str) -> str:
    """Verify a Cognito ID token and return its stable user identifier."""
    try:
        signing_key = JWKS_CLIENT.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "token_use"]},
        )
    except InvalidTokenError as error:
        raise deny("invalid token") from error
    if claims.get("token_use") != "id":
        raise deny("an ID token is required")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise deny("token has no subject")
    return subject


def allow_policy(event: dict[str, Any], subject: str) -> dict[str, Any]:
    resource = event.get("methodArn") or event.get("routeArn")
    if not isinstance(resource, str) or not resource:
        raise deny("request resource is missing")
    return {
        "principalId": subject,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": "Allow", "Resource": resource}],
        },
        "context": {"sub": subject},
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Validate the `$connect` query token without exposing it in logs."""
    try:
        return allow_policy(event, validated_subject(token_from_event(event)))
    except Exception as error:
        # Do not log exception chaining: it can contain malformed token text.
        print(f"Rejected WebSocket connection: {str(error).split(':', 1)[0]}")
        raise Exception("Unauthorized") from None
