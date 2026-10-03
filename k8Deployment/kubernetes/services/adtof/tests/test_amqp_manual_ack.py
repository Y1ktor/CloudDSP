"""Unit tests for one ADTOF Pika-shaped manual-ack receive attempt.

Channels and the parse-and-claim bridge are mocked. Tests open no RabbitMQ,
PostgreSQL, or MinIO connection; they do not start a loop, invoke ADTOF, build
an image, or create Kubernetes state. They prove broker acknowledgement policy
for durable, malformed, and retryable outcomes only.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.messaging.adtof_requested_message import ADTOFRequestContractError, ADTOFRequestedMessage
from app.messaging.amqp_connection import ADTOF_REQUEST_QUEUE
from app.messaging.amqp_manual_ack import (
    ADTOFAMQPUnavailable,
    ADTOFConsumeOneOutcome,
    ADTOFConsumeOneResult,
    consume_one_adtof_requested_delivery,
)
from app.runtime.delivery_claim import ADTOFDeliveryClaim
from app.db.postgresql import ADTOFDatabaseUnavailable
from app.db.task_claim import (
    ADTOFStaleRequestReason,
    ADTOFTaskClaimDisposition,
    ADTOFTaskClaimInconsistency,
    ADTOFTaskClaimResult,
    ADTOFTaskClaimProtocolError,
    ADTOFTaskLease,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def claimed_delivery(*, disposition: ADTOFTaskClaimDisposition) -> ADTOFDeliveryClaim:
    """Return a committed bridge result without contacting external services."""

    claim_kwargs: dict[str, object] = {"disposition": disposition}
    if disposition is ADTOFTaskClaimDisposition.CLAIMED:
        claim_kwargs["lease"] = ADTOFTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            stem_name="drums",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=datetime(2026, 9, 13, 12, 15, tzinfo=UTC),
        )
    elif disposition is ADTOFTaskClaimDisposition.DUPLICATE:
        claim_kwargs["duplicate_status"] = "running"
    elif disposition is ADTOFTaskClaimDisposition.STALE:
        claim_kwargs["stale_reason"] = ADTOFStaleRequestReason.JOB_EXPIRED
    return ADTOFDeliveryClaim(
        message=ADTOFRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="drums",
            stem_bucket="clouddsp-uploads",
            stem_object_key=f"stems/{JOB_ID}/drums.wav",
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
        task_claim=ADTOFTaskClaimResult(**claim_kwargs),  # type: ignore[arg-type]
    )


class ADTOFManualAcknowledgementTests(unittest.TestCase):
    """Prove only committed durable facts remove an ADTOF request delivery."""

    def setUp(self) -> None:
        """Give each test one Pika-shaped channel and valid method frame."""

        self.channel = MagicMock()
        self.method = SimpleNamespace(
            exchange="clouddsp.processing-events",
            routing_key="adtof.requested",
            delivery_tag=42,
        )

    def _delivery(self, *, body: object = b"{}") -> None:
        """Make ``basic_get`` return one unacknowledged synthetic delivery."""

        self.channel.basic_get.return_value = (self.method, SimpleNamespace(), body)

    def test_empty_queue_is_idle_without_bridge_or_broker_action(self) -> None:
        """A later supervisor sleeps only after RabbitMQ has assigned no work."""

        self.channel.basic_get.return_value = (None, None, None)
        database = object()

        with patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery") as bridge:
            result = consume_one_adtof_requested_delivery(
                self.channel,
                database=database,  # type: ignore[arg-type]
            )

        self.assertEqual(result.outcome, ADTOFConsumeOneOutcome.IDLE)
        self.assertIsNone(result.lease)
        self.assertIsNone(result.message)
        self.channel.basic_get.assert_called_once_with(queue=ADTOF_REQUEST_QUEUE, auto_ack=False)
        bridge.assert_not_called()
        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_committed_results_ack_with_lease_exposed_only_for_new_claim(self, bridge) -> None:
        """Duplicate/stale history cannot reach later ADTOF CPU execution."""

        self._delivery()
        database = object()
        for disposition in ADTOFTaskClaimDisposition:
            with self.subTest(disposition=disposition):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.return_value = claimed_delivery(disposition=disposition)
                self.channel.basic_ack.reset_mock()

                result = consume_one_adtof_requested_delivery(
                    self.channel,
                    database=database,  # type: ignore[arg-type]
                )

                expected = claimed_delivery(disposition=disposition)
                self.assertEqual(
                    result.outcome,
                    (
                        ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE
                        if disposition is ADTOFTaskClaimDisposition.CLAIMED
                        else ADTOFConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
                    ),
                )
                self.assertEqual(result.lease, expected.task_claim.lease)
                self.assertEqual(
                    result.message,
                    expected.message if disposition is ADTOFTaskClaimDisposition.CLAIMED else None,
                )
                bridge.assert_called_once_with(
                    database=database,
                    delivery_exchange="clouddsp.processing-events",
                    delivery_routing_key="adtof.requested",
                    properties=self.channel.basic_get.return_value[1],
                    body=b"{}",
                )
                self.channel.basic_ack.assert_called_once_with(42)
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_malformed_contract_nacks_without_requeue_and_never_acknowledges(self, bridge) -> None:
        """Permanent parser faults use the main queue's configured DLQ path."""

        self._delivery(body=b"not-json")
        bridge.side_effect = ADTOFRequestContractError("ADTOF request delivery is invalid.")

        result = consume_one_adtof_requested_delivery(
            self.channel,
            database=object(),  # type: ignore[arg-type]
        )

        self.assertEqual(result.outcome, ADTOFConsumeOneOutcome.MALFORMED_REJECTED)
        self.assertIsNone(result.lease)
        self.assertIsNone(result.message)
        self.channel.basic_nack.assert_called_once_with(42, requeue=False)
        self.channel.basic_ack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_database_or_durable_identity_failure_remains_unacknowledged(self, bridge) -> None:
        """Only a later terminal policy may make a durable conflict ack-safe."""

        self._delivery()
        for error in (
            ADTOFDatabaseUnavailable("PostgreSQL ADTOF task access is unavailable."),
            ADTOFTaskClaimInconsistency("ADTOF request conflicts with durable state."),
        ):
            with self.subTest(error=type(error).__name__):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.side_effect = error
                self.channel.basic_ack.reset_mock()
                self.channel.basic_nack.reset_mock()

                with self.assertRaises(type(error)):
                    consume_one_adtof_requested_delivery(
                        self.channel,
                        database=object(),  # type: ignore[arg-type]
                    )

                self.channel.basic_ack.assert_not_called()
                self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_claim_result_missing_lease_is_not_acknowledged(self, bridge) -> None:
        """A regression cannot ack work while losing its durable ownership token."""

        self._delivery()
        bridge.return_value = ADTOFDeliveryClaim(
            message=claimed_delivery(disposition=ADTOFTaskClaimDisposition.CLAIMED).message,
            task_claim=ADTOFTaskClaimResult(disposition=ADTOFTaskClaimDisposition.CLAIMED),
        )

        with self.assertRaises(ADTOFTaskClaimProtocolError):
            consume_one_adtof_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_claim_result_missing_validated_message_is_not_acknowledged(self, bridge) -> None:
        """A claimed lease cannot run if parser evidence was lost or malformed."""

        self._delivery()
        bridge.return_value = ADTOFDeliveryClaim(
            message=object(),  # type: ignore[arg-type] - tests a bypassed dataclass constructor.
            task_claim=claimed_delivery(
                disposition=ADTOFTaskClaimDisposition.CLAIMED
            ).task_claim,
        )

        with self.assertRaises(ADTOFTaskClaimProtocolError):
            consume_one_adtof_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_acknowledgement_failure_is_retryable_without_nacking_the_committed_claim(self, bridge) -> None:
        """A lost ack makes a redelivery duplicate-safe rather than a second claim."""

        self._delivery()
        bridge.return_value = claimed_delivery(disposition=ADTOFTaskClaimDisposition.DUPLICATE)
        self.channel.basic_ack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(ADTOFAMQPUnavailable) as raised:
            consume_one_adtof_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.assertEqual(str(raised.exception), "RabbitMQ ADTOF acknowledgement is unavailable.")
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_adtof_requested_delivery")
    def test_rejection_failure_leaves_malformed_delivery_unacknowledged(self, bridge) -> None:
        """A failed nack cannot silently discard malformed durable evidence."""

        self._delivery()
        bridge.side_effect = ADTOFRequestContractError("ADTOF request delivery is invalid.")
        self.channel.basic_nack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(ADTOFAMQPUnavailable) as raised:
            consume_one_adtof_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.assertEqual(str(raised.exception), "RabbitMQ ADTOF rejection is unavailable.")
        self.channel.basic_ack.assert_not_called()


class ADTOFConsumeOneResultTests(unittest.TestCase):
    """Prove result outcomes cannot be paired with unsafe execution evidence."""

    def test_outcome_lease_pairing_is_checked_at_result_construction(self) -> None:
        """Only a successful broker acknowledgement may pass a lease onward."""

        lease = claimed_delivery(
            disposition=ADTOFTaskClaimDisposition.CLAIMED
        ).task_claim.lease
        assert lease is not None
        message = claimed_delivery(
            disposition=ADTOFTaskClaimDisposition.CLAIMED
        ).message
        for outcome, paired_lease, paired_message in (
            (ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE, None, None),
            (ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE, lease, None),
            (ADTOFConsumeOneOutcome.ACKNOWLEDGED_LEASE, None, message),
            (ADTOFConsumeOneOutcome.IDLE, lease, None),
            (ADTOFConsumeOneOutcome.IDLE, None, message),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    ADTOFConsumeOneResult(
                        outcome=outcome,
                        lease=paired_lease,
                        message=paired_message,
                    )


if __name__ == "__main__":
    unittest.main()
