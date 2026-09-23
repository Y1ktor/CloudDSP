"""Unit tests for the six-stem-load Keycloak-only lifecycle boundary."""

from __future__ import annotations

import json
from typing import Mapping
import unittest
from urllib.parse import parse_qs

from keycloak_identity_lifecycle import (
    HttpResponse,
    IdentityLifecycleError,
    KeycloakLoadIdentityLifecycle,
    LoadTestIdentity,
)


_CLIENT_UUID = "11111111-1111-4111-8111-111111111111"
_USER_UUID = "22222222-2222-4222-8222-222222222222"


class RecordingTransport:
    """Return preplanned bounded responses while recording secret-free request shapes."""

    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "operation": operation,
                "url": url,
                "expected_statuses": expected_statuses,
                "method": method,
                "data": data,
                "headers": dict(headers or {}),
            }
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status: int, body: object = None, headers: Mapping[str, str] | None = None) -> HttpResponse:
    """Build one fake HTTP response without accidentally using a real network call."""

    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    return HttpResponse(status=status, headers=dict(headers or {}), body=encoded)


def lifecycle(transport: RecordingTransport) -> KeycloakLoadIdentityLifecycle:
    """Build the adapter with clearly fake credentials that tests never display."""

    return KeycloakLoadIdentityLifecycle(
        keycloak_internal_base_url="http://clouddsp-keycloak:8080",
        administrator_realm="master",
        application_realm="clouddsp",
        job_api_client_id="clouddsp-job-api",
        bootstrap_admin_username="bootstrap-user",
        bootstrap_admin_password="bootstrap-password",
        transport=transport,
    )


class KeycloakLoadIdentityLifecycleTests(unittest.TestCase):
    """Prove direct-grant setup and cleanup without a Keycloak server."""

    def test_create_uses_isolated_direct_grant_client_and_job_api_audience(self) -> None:
        transport = RecordingTransport(
            [
                response(200, {"access_token": "admin-token"}),
                response(201, headers={"Location": f"http://keycloak/admin/clients/{_CLIENT_UUID}"}),
                response(201),
                response(201, headers={"Location": f"http://keycloak/admin/users/{_USER_UUID}"}),
                response(204),
            ]
        )

        identity = lifecycle(transport).create(run_marker="loadrun01")

        self.assertEqual(identity.client_id, "clouddsp-six-stem-load-loadrun01")
        self.assertEqual(identity.username, "six-stem-load-loadrun01")
        self.assertEqual(identity.user_id, _USER_UUID)
        self.assertNotIn(identity.password, repr(identity))
        self.assertEqual(len(transport.calls), 5)

        client_payload = json.loads(transport.calls[1]["data"])
        self.assertTrue(client_payload["publicClient"])
        self.assertFalse(client_payload["standardFlowEnabled"])
        self.assertFalse(client_payload["implicitFlowEnabled"])
        self.assertTrue(client_payload["directAccessGrantsEnabled"])
        self.assertFalse(client_payload["serviceAccountsEnabled"])
        self.assertFalse(client_payload["fullScopeAllowed"])
        self.assertEqual(client_payload["redirectUris"], [])
        self.assertEqual(client_payload["webOrigins"], [])

        mapper_payload = json.loads(transport.calls[2]["data"])
        self.assertEqual(mapper_payload["protocolMapper"], "oidc-audience-mapper")
        self.assertEqual(
            mapper_payload["config"]["included.client.audience"], "clouddsp-job-api"
        )

        user_payload = json.loads(transport.calls[3]["data"])
        self.assertTrue(user_payload["emailVerified"])
        self.assertTrue(user_payload["enabled"])
        password_payload = json.loads(transport.calls[4]["data"])
        self.assertFalse(password_payload["temporary"])
        self.assertEqual(password_payload["value"], identity.password)

    def test_create_failure_removes_created_user_and_client(self) -> None:
        transport = RecordingTransport(
            [
                response(200, {"access_token": "admin-token"}),
                response(201, headers={"Location": f"http://keycloak/admin/clients/{_CLIENT_UUID}"}),
                response(201),
                response(201, headers={"Location": f"http://keycloak/admin/users/{_USER_UUID}"}),
                IdentityLifecycleError("password setup returned HTTP 500"),
                response(204),
                response(204),
            ]
        )

        with self.assertRaisesRegex(IdentityLifecycleError, "password setup returned HTTP 500"):
            lifecycle(transport).create(run_marker="loadrun01")

        self.assertEqual([call["method"] for call in transport.calls[-2:]], ["DELETE", "DELETE"])
        self.assertIn(f"/users/{_USER_UUID}", transport.calls[-2]["url"])
        self.assertIn(f"/clients/{_CLIENT_UUID}", transport.calls[-1]["url"])

    def test_revoke_uses_fresh_admin_token_and_attempts_both_deletions(self) -> None:
        transport = RecordingTransport(
            [
                response(200, {"access_token": "new-admin-token"}),
                IdentityLifecycleError("temporary user delete returned HTTP 500"),
                response(204),
            ]
        )
        identity = LoadTestIdentity(
            run_marker="loadrun01",
            client_id="clouddsp-six-stem-load-loadrun01",
            client_uuid=_CLIENT_UUID,
            user_id=_USER_UUID,
            username="six-stem-load-loadrun01",
            email="six-stem-load-loadrun01@clouddsp.test",
            password="temporary-password",
        )

        with self.assertRaisesRegex(IdentityLifecycleError, "identity cleanup failed"):
            lifecycle(transport).revoke(identity=identity)

        self.assertEqual([call["method"] for call in transport.calls], ["POST", "DELETE", "DELETE"])
        self.assertIn(f"/users/{_USER_UUID}", transport.calls[1]["url"])
        self.assertIn(f"/clients/{_CLIENT_UUID}", transport.calls[2]["url"])

    def test_password_grant_uses_only_the_temporary_identity_values(self) -> None:
        transport = RecordingTransport([response(200, {"access_token": "user-token"})])
        identity = LoadTestIdentity(
            run_marker="loadrun01",
            client_id="clouddsp-six-stem-load-loadrun01",
            client_uuid=_CLIENT_UUID,
            user_id=_USER_UUID,
            username="six-stem-load-loadrun01",
            email="six-stem-load-loadrun01@clouddsp.test",
            password="temporary-password",
        )

        self.assertEqual(lifecycle(transport).request_user_access_token(identity=identity), "user-token")

        form = parse_qs(transport.calls[0]["data"].decode("utf-8"), strict_parsing=True)
        self.assertEqual(form["grant_type"], ["password"])
        self.assertEqual(form["client_id"], [identity.client_id])
        self.assertEqual(form["username"], [identity.username])
        self.assertEqual(form["password"], [identity.password])
        self.assertNotIn("bootstrap-user", transport.calls[0]["data"].decode("utf-8"))

    def test_malformed_run_marker_fails_before_any_http_request(self) -> None:
        transport = RecordingTransport([])

        with self.assertRaisesRegex(ValueError, "run_marker"):
            lifecycle(transport).create(run_marker="not/a-safe-marker")

        self.assertEqual(transport.calls, [])


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
