"""Offline contract tests for broker-side owner-bound load coordinate verification."""

from __future__ import annotations

import json
from threading import Lock
from typing import Mapping
import unittest
from urllib.parse import parse_qs

from keycloak_identity_lifecycle import HttpResponse, LoadTestIdentity
from lifecycle_handoff import AuthenticatedLoadCoordinates, SubmittedLoadJobCoordinate
from owner_bound_job_verifier import (
    OwnerBoundJobVerificationError,
    OwnerBoundJobVerificationSettings,
    verify_owner_bound_load_jobs,
)


_SUBJECT = "11111111-1111-4111-8111-111111111111"
_JOB_IDS = (
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
)


def settings() -> OwnerBoundJobVerificationSettings:
    """Provide only the reviewed private Keycloak and Job API routes."""

    return OwnerBoundJobVerificationSettings(
        keycloak_internal_base_url="http://clouddsp-keycloak.clouddsp-data.svc:8080",
        application_realm="clouddsp",
        job_api_internal_base_url="http://clouddsp-job-api.clouddsp-app.svc:80",
    )


def identity() -> LoadTestIdentity:
    """Model the broker-only disposable identity without a live Keycloak call."""

    return LoadTestIdentity(
        run_marker="loadrun01",
        client_id="clouddsp-six-stem-load-loadrun01",
        client_uuid="55555555-5555-4555-8555-555555555555",
        user_id="66666666-6666-4666-8666-666666666666",
        username="six-stem-load-loadrun01",
        email="six-stem-load-loadrun01@clouddsp.test",
        password="temporary-user-password",
    )


def coordinates() -> AuthenticatedLoadCoordinates:
    """Return three strict handoff coordinates with no object key or access token."""

    return AuthenticatedLoadCoordinates(
        run_marker="loadrun01",
        subject=_SUBJECT,
        stem_mode="6-stems",
        jobs=tuple(
            SubmittedLoadJobCoordinate(
                ordinal=ordinal,
                job_id=job_id,
                source_filename=f"six-stem-load-loadrun01-{ordinal}.wav",
                source_size_bytes=352844,
                source_sha256=f"{ordinal:x}" * 64,
            )
            for ordinal, job_id in enumerate(_JOB_IDS, start=1)
        ),
    )


class OwnerBoundVerificationTransport:
    """Thread-safe fake transport that records only reviewed Keycloak/API calls."""

    def __init__(self, *, returned_subject: str = _SUBJECT, wrong_filename: bool = False) -> None:
        self._lock = Lock()
        self.calls: list[dict[str, object]] = []
        self._returned_subject = returned_subject
        self._wrong_filename = wrong_filename

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
        with self._lock:
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
        if operation == "six-stem broker temporary-user token request":
            return _response({"access_token": "fresh-broker-user-token"})
        if operation == "six-stem broker authenticated identity request":
            return _response({"subject": self._returned_subject})
        if operation.startswith("six-stem broker owner-bound Job verification "):
            ordinal = int(operation.rsplit(" ", 1)[-1])
            job = coordinates().jobs[ordinal - 1]
            return _response(
                {
                    "job_id": job.job_id,
                    "source_type": "direct_upload",
                    "source_filename": "wrong.wav" if self._wrong_filename else job.source_filename,
                    "source_content_type": "audio/wav",
                    "source_size_bytes": job.source_size_bytes,
                    "source_uploaded": True,
                    "stem_mode": "6-stems",
                    "status": "source_uploaded",
                    "revision": 2,
                    "stems": {},
                    "midi": {},
                }
            )
        raise AssertionError(f"Unexpected operation: {operation}")


def _response(payload: object) -> HttpResponse:
    """Build a compact fake successful JSON response."""

    return HttpResponse(status=200, headers={}, body=json.dumps(payload).encode("utf-8"))


class OwnerBoundJobVerifierTests(unittest.TestCase):
    """The broker must prove owner visibility, never trust coordinates blindly."""

    def test_fresh_user_token_and_exactly_three_owner_bound_reads_verify_the_claim(self) -> None:
        """No list endpoint, database path, MinIO call, or queue shortcut is available."""

        transport = OwnerBoundVerificationTransport()
        verified = verify_owner_bound_load_jobs(
            settings=settings(), identity=identity(), coordinates=coordinates(), transport=transport
        )

        self.assertEqual(verified.subject, _SUBJECT)
        self.assertEqual(verified.coordinates, coordinates())
        operations = [call["operation"] for call in transport.calls]
        self.assertEqual(operations.count("six-stem broker temporary-user token request"), 1)
        self.assertEqual(operations.count("six-stem broker authenticated identity request"), 1)
        self.assertEqual(
            [item for item in operations if "owner-bound Job verification" in item],
            [
                "six-stem broker owner-bound Job verification 1",
                "six-stem broker owner-bound Job verification 2",
                "six-stem broker owner-bound Job verification 3",
            ],
        )
        self.assertFalse(any("/jobs?" in str(call["url"]) for call in transport.calls))

        token_call = transport.calls[0]
        token_form = parse_qs(token_call["data"].decode("utf-8"), strict_parsing=True)
        self.assertEqual(token_form["client_id"], [identity().client_id])
        self.assertEqual(token_form["username"], [identity().username])
        self.assertEqual(token_form["password"], [identity().password])
        self.assertNotIn("KC_BOOTSTRAP_ADMIN", token_call["data"].decode("utf-8"))

    def test_subject_mismatch_stops_before_any_job_is_read(self) -> None:
        """A forged coordinate owner cannot lead to a later observer capability."""

        transport = OwnerBoundVerificationTransport(
            returned_subject="77777777-7777-4777-8777-777777777777"
        )

        with self.assertRaisesRegex(OwnerBoundJobVerificationError, "did not match"):
            verify_owner_bound_load_jobs(
                settings=settings(), identity=identity(), coordinates=coordinates(), transport=transport
            )

        self.assertFalse(any("owner-bound Job verification" in call["operation"] for call in transport.calls))

    def test_snapshot_mismatch_rejects_the_coordinate_before_any_capability_exists(self) -> None:
        """One wrong filename is sufficient to reject the whole claimed run."""

        transport = OwnerBoundVerificationTransport(wrong_filename=True)

        with self.assertRaisesRegex(OwnerBoundJobVerificationError, "did not match"):
            verify_owner_bound_load_jobs(
                settings=settings(), identity=identity(), coordinates=coordinates(), transport=transport
            )

    def test_settings_reject_browser_or_public_endpoint_substitution(self) -> None:
        """The broker cannot be redirected to a browser host by a future manifest edit."""

        with self.assertRaisesRegex(OwnerBoundJobVerificationError, "Job API endpoint"):
            OwnerBoundJobVerificationSettings(
                keycloak_internal_base_url="http://clouddsp-keycloak.clouddsp-data.svc:8080",
                application_realm="clouddsp",
                job_api_internal_base_url="http://clouddsp.localhost:8080",
            )


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
