"""Unit tests for the ADTOF parser-to-first-claim bridge.

Pika-shaped properties and the first-claim composition are mocked. These tests
open no broker, PostgreSQL, or MinIO connection; they do not acknowledge a
delivery, run ADTOF, sleep, build an image, or create Kubernetes state. They
prove parse-before-claim ordering and the bridge's deliberate absence of AMQP
policy.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.adtof_requested_message import (
    ADTOF_REQUESTED_ROUTING_KEY,
    PROCESSING_EXCHANGE,
    ADTOFRequestContractError,
    ADTOFRequestedMessage,
)
from app.delivery_claim import ADTOFDeliveryClaim, claim_adtof_requested_delivery
from app.postgresql import ADTOFDatabaseUnavailable
from app.task_claim import (
    ADTOFStaleRequestReason,
    ADTOFTaskClaimDisposition,
    ADTOFTaskClaimResult,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def properties() -> SimpleNamespace:
    """Return only the persistent AMQP properties required by the parser."""

    return SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        type="adtof.requested",
        message_id=EVENT_ID,
        correlation_id=JOB_ID,
    )


def body() -> bytes:
    """Return a compact version-one ADTOF request without private audio bytes."""

    return json.dumps(
        {
            "schema_version": 1,
            "job_id": JOB_ID,
            "stem_name": "drums",
            "stem": {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{JOB_ID}/drums.wav",
                "content_type": "audio/wav",
                "size_bytes": 101,
                "sha256": "a" * 64,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def parsed_message() -> ADTOFRequestedMessage:
    """Return the strict private identifiers expected after parser success."""

    return ADTOFRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stem_name="drums",
        stem_bucket="clouddsp-uploads",
        stem_object_key=f"stems/{JOB_ID}/drums.wav",
        stem_content_length=101,
        stem_sha256="a" * 64,
    )


class ADTOFDeliveryClaimBridgeTests(unittest.TestCase):
    """Prove parsing precedes durable work without embedding RabbitMQ policy."""

    def _run(self, *, database: object = object(), **overrides: object) -> ADTOFDeliveryClaim:
        """Call the bridge with a valid request unless a test narrows one input."""

        return claim_adtof_requested_delivery(
            database=database,  # type: ignore[arg-type] - the mocked claim does not inspect it.
            delivery_exchange=overrides.pop("delivery_exchange", PROCESSING_EXCHANGE),
            delivery_routing_key=overrides.pop("delivery_routing_key", ADTOF_REQUESTED_ROUTING_KEY),
            properties=overrides.pop("properties", properties()),
            body=overrides.pop("body", body()),
            lease_seconds=overrides.pop("lease_seconds", 900),
            uuid_factory=overrides.pop("uuid_factory", lambda: UUID(LEASE_TOKEN)),
            **overrides,
        )

    @patch("app.delivery_claim.claim_first_adtof_task")
    def test_valid_contract_passes_only_parsed_identifiers_to_committed_claim(self, claim) -> None:
        """Raw body/properties never cross into the PostgreSQL decision layer."""

        durable_result = ADTOFTaskClaimResult(
            disposition=ADTOFTaskClaimDisposition.DUPLICATE,
            duplicate_status="running",
        )
        claim.return_value = durable_result
        database = object()
        uuid_factory = lambda: UUID(LEASE_TOKEN)

        result = self._run(database=database, uuid_factory=uuid_factory)

        self.assertEqual(result.message, parsed_message())
        self.assertIs(result.task_claim, durable_result)
        claim.assert_called_once_with(
            database=database,
            message=parsed_message(),
            lease_seconds=900,
            uuid_factory=uuid_factory,
        )

    @patch("app.delivery_claim.claim_first_adtof_task")
    def test_malformed_contract_never_attempts_a_database_claim(self, claim) -> None:
        """The later manual-ack layer alone decides the permanent DLQ action."""

        with self.assertRaises(ADTOFRequestContractError):
            self._run(body=b"not-json")

        claim.assert_not_called()

    @patch("app.delivery_claim.claim_first_adtof_task")
    def test_database_outage_propagates_after_valid_parse_without_transport_action(self, claim) -> None:
        """The future AMQP adapter must leave an incomplete claim unacknowledged."""

        claim.side_effect = ADTOFDatabaseUnavailable("PostgreSQL ADTOF task access is unavailable.")

        with self.assertRaises(ADTOFDatabaseUnavailable):
            self._run()

        claim.assert_called_once()

    @patch("app.delivery_claim.claim_first_adtof_task")
    def test_stale_result_is_preserved_without_an_acknowledgement_boolean(self, claim) -> None:
        """Only the later transport maps a committed durable fact to RabbitMQ."""

        claim.return_value = ADTOFTaskClaimResult(
            disposition=ADTOFTaskClaimDisposition.STALE,
            stale_reason=ADTOFStaleRequestReason.JOB_EXPIRED,
        )

        result = self._run()

        self.assertEqual(result.task_claim.disposition, ADTOFTaskClaimDisposition.STALE)
        self.assertEqual(result.task_claim.stale_reason, ADTOFStaleRequestReason.JOB_EXPIRED)
        # A durable fact is not a misleading `safe_to_acknowledge` flag. The
        # separate transport adapter owns acknowledgement policy explicitly.
        self.assertFalse(hasattr(result, "safe_to_acknowledge"))


if __name__ == "__main__":
    unittest.main()
