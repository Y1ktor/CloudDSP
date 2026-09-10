"""Unit tests for the Demucs AMQP-contract-to-durable-claim bridge.

The tests use Pika-shaped properties and a mocked first-claim function only.
They do not open RabbitMQ/PostgreSQL/MinIO, acknowledge or reject a delivery,
run Demucs, sleep, or create a Kubernetes resource.  They prove the bridge's
ordering and its deliberate absence of broker policy.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.delivery_claim import DemucsDeliveryClaim, claim_demucs_requested_delivery
from app.demucs_requested_message import (
    DEMUCS_REQUESTED_ROUTING_KEY,
    PROCESSING_EXCHANGE,
    DemucsRequestContractError,
    DemucsRequestedMessage,
)
from app.postgresql import DemucsDatabaseUnavailable
from app.task_lease import (
    DemucsStaleRequestReason,
    DemucsTaskClaimDisposition,
    DemucsTaskClaimResult,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def properties() -> SimpleNamespace:
    """Return only the fixed persistent AMQP properties expected by the parser."""

    return SimpleNamespace(
        content_type="application/json",
        content_encoding="utf-8",
        delivery_mode=2,
        type="demucs.requested",
        message_id=EVENT_ID,
        correlation_id=JOB_ID,
    )


def body() -> bytes:
    """Return the compact version-1 dispatcher body without private audio bytes."""

    return json.dumps(
        {
            "schema_version": 1,
            "job_id": JOB_ID,
            "source": {
                "bucket": "clouddsp-uploads",
                "object_key": f"uploads/{JOB_ID}/mix.wav",
            },
            "stem_mode": "4-stems",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def parsed_message() -> DemucsRequestedMessage:
    """Return the identifiers expected after the real contract parser succeeds."""

    return DemucsRequestedMessage(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        source_bucket="clouddsp-uploads",
        source_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
    )


class DeliveryClaimBridgeTests(unittest.TestCase):
    """Prove parse-before-claim ordering without embedding AMQP ack policy."""

    def _run(self, *, database: object = object(), **overrides: object) -> DemucsDeliveryClaim:
        """Call the bridge with a normal broker contract unless a test narrows it."""

        return claim_demucs_requested_delivery(
            database=database,  # type: ignore[arg-type] - mocked claim does not inspect it.
            delivery_exchange=overrides.pop("delivery_exchange", PROCESSING_EXCHANGE),
            delivery_routing_key=overrides.pop("delivery_routing_key", DEMUCS_REQUESTED_ROUTING_KEY),
            properties=overrides.pop("properties", properties()),
            body=overrides.pop("body", body()),
            lease_seconds=overrides.pop("lease_seconds", 900),
            uuid_factory=overrides.pop("uuid_factory", lambda: UUID(LEASE_TOKEN)),
            **overrides,
        )

    @patch("app.delivery_claim.claim_first_demucs_task")
    def test_valid_contract_passes_only_parsed_identifiers_to_committed_claim(self, claim) -> None:
        """Raw body/properties never cross into the database decision layer."""

        durable_result = DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.DUPLICATE,
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

    @patch("app.delivery_claim.claim_first_demucs_task")
    def test_malformed_contract_never_attempts_a_database_claim(self, claim) -> None:
        """The future transport can separately DLQ this permanent parser error."""

        with self.assertRaises(DemucsRequestContractError):
            self._run(body=b"not-json")

        claim.assert_not_called()

    @patch("app.delivery_claim.claim_first_demucs_task")
    def test_database_outage_propagates_after_a_valid_parse_without_transport_action(self, claim) -> None:
        """The future Pika adapter must leave this delivery unacknowledged for retry."""

        claim.side_effect = DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable.")

        with self.assertRaises(DemucsDatabaseUnavailable):
            self._run()

        claim.assert_called_once()

    @patch("app.delivery_claim.claim_first_demucs_task")
    def test_durable_stale_result_is_preserved_without_an_acknowledgement_flag(self, claim) -> None:
        """Only the later transport maps durable outcomes to RabbitMQ operations."""

        claim.return_value = DemucsTaskClaimResult(
            disposition=DemucsTaskClaimDisposition.STALE,
            stale_reason=DemucsStaleRequestReason.JOB_EXPIRED,
        )

        result = self._run()

        self.assertEqual(result.task_claim.disposition, DemucsTaskClaimDisposition.STALE)
        self.assertEqual(result.task_claim.stale_reason, DemucsStaleRequestReason.JOB_EXPIRED)
        # The bridge exposes a durable fact, not a misleading boolean such as
        # `safe_to_acknowledge`; policy belongs to the future Pika-only layer.
        self.assertFalse(hasattr(result, "safe_to_acknowledge"))


if __name__ == "__main__":
    unittest.main()
