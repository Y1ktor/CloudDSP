"""Unit tests for one Basic Pitch Pika-shaped manual-ack receive attempt.

Every channel and bridge is mocked. The tests open no RabbitMQ/PostgreSQL/
MinIO connection, do not start a consumer loop, run no Basic Pitch process, and
do not create Docker or Kubernetes state. They prove the acknowledgement policy
for durable, malformed, and retryable outcomes.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import (
    BasicPitchAMQPUnavailable,
    BasicPitchConsumeOneOutcome,
    BasicPitchConsumeOneResult,
    consume_one_basic_pitch_requested_delivery,
)
from app.messaging.amqp_connection import BASIC_PITCH_REQUEST_QUEUE
from app.messaging.basic_pitch_requested_message import BasicPitchRequestContractError, BasicPitchRequestedMessage
from app.runtime.delivery_claim import BasicPitchDeliveryClaim
from app.db.postgresql import BasicPitchDatabaseUnavailable
from app.db.task_lease import (
    BasicPitchStaleRequestReason,
    BasicPitchTaskClaimDisposition,
    BasicPitchTaskClaimInconsistency,
    BasicPitchTaskClaimResult,
    BasicPitchTaskLease,
    BasicPitchTaskLeaseProtocolError,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def claimed_delivery(*, disposition: BasicPitchTaskClaimDisposition) -> BasicPitchDeliveryClaim:
    """Return a committed bridge result without connecting to external services."""

    claim_kwargs: dict[str, object] = {"disposition": disposition}
    if disposition is BasicPitchTaskClaimDisposition.CLAIMED:
        claim_kwargs["lease"] = BasicPitchTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"stems/{JOB_ID}/vocals.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=datetime(2026, 9, 11, 12, 15, tzinfo=UTC),
        )
    elif disposition is BasicPitchTaskClaimDisposition.DUPLICATE:
        claim_kwargs["duplicate_status"] = "running"
    elif disposition is BasicPitchTaskClaimDisposition.STALE:
        claim_kwargs["stale_reason"] = BasicPitchStaleRequestReason.JOB_EXPIRED
    return BasicPitchDeliveryClaim(
        message=BasicPitchRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            stem_name="vocals",
            stem_bucket="clouddsp-uploads",
            stem_object_key=f"stems/{JOB_ID}/vocals.wav",
            stem_content_length=101,
            stem_sha256="a" * 64,
        ),
        task_claim=BasicPitchTaskClaimResult(**claim_kwargs),  # type: ignore[arg-type]
    )


class BasicPitchManualAcknowledgementTests(unittest.TestCase):
    """Prove only durable-safe claim outcomes remove a request delivery."""

    def setUp(self) -> None:
        """Give each test one Pika-shaped channel and Basic Pitch method frame."""

        self.channel = MagicMock()
        self.method = SimpleNamespace(
            exchange="clouddsp.processing-events",
            routing_key="basic-pitch.requested",
            delivery_tag=42,
        )

    def _delivery(self, *, body: object = b"{}") -> None:
        """Make `basic_get` return exactly one unacknowledged fake delivery."""

        self.channel.basic_get.return_value = (self.method, SimpleNamespace(), body)

    def test_empty_queue_is_idle_without_bridge_or_broker_action(self) -> None:
        """A future supervisor sleeps only when the broker assigned no work."""

        self.channel.basic_get.return_value = (None, None, None)
        database = object()

        with patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery") as bridge:
            result = consume_one_basic_pitch_requested_delivery(
                self.channel,
                database=database,  # type: ignore[arg-type]
            )

        self.assertEqual(result.outcome, BasicPitchConsumeOneOutcome.IDLE)
        self.assertIsNone(result.lease)
        self.assertIsNone(result.message)
        self.channel.basic_get.assert_called_once_with(queue=BASIC_PITCH_REQUEST_QUEUE, auto_ack=False)
        bridge.assert_not_called()
        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_committed_results_ack_with_lease_exposed_only_for_new_claim(self, bridge) -> None:
        """Duplicate/stale history cannot reach later Basic Pitch model execution."""

        self._delivery()
        database = object()
        for disposition in BasicPitchTaskClaimDisposition:
            with self.subTest(disposition=disposition):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.return_value = claimed_delivery(disposition=disposition)
                self.channel.basic_ack.reset_mock()

                result = consume_one_basic_pitch_requested_delivery(
                    self.channel,
                    database=database,  # type: ignore[arg-type]
                )

                expected = claimed_delivery(disposition=disposition)
                self.assertEqual(
                    result.outcome,
                    (
                        BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE
                        if disposition is BasicPitchTaskClaimDisposition.CLAIMED
                        else BasicPitchConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
                    ),
                )
                self.assertEqual(result.lease, expected.task_claim.lease)
                self.assertEqual(
                    result.message,
                    expected.message if disposition is BasicPitchTaskClaimDisposition.CLAIMED else None,
                )
                bridge.assert_called_once_with(
                    database=database,
                    delivery_exchange="clouddsp.processing-events",
                    delivery_routing_key="basic-pitch.requested",
                    properties=self.channel.basic_get.return_value[1],
                    body=b"{}",
                )
                self.channel.basic_ack.assert_called_once_with(42)
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_malformed_contract_nacks_without_requeue_and_never_acknowledges(self, bridge) -> None:
        """Permanent parser faults use the main queue's configured dead-letter path."""

        self._delivery(body=b"not-json")
        bridge.side_effect = BasicPitchRequestContractError("Basic Pitch request delivery is invalid.")

        result = consume_one_basic_pitch_requested_delivery(
            self.channel,
            database=object(),  # type: ignore[arg-type]
        )

        self.assertEqual(result.outcome, BasicPitchConsumeOneOutcome.MALFORMED_REJECTED)
        self.assertIsNone(result.lease)
        self.assertIsNone(result.message)
        self.channel.basic_nack.assert_called_once_with(42, requeue=False)
        self.channel.basic_ack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_database_or_durable_identity_failure_remains_unacknowledged(self, bridge) -> None:
        """Only a later terminal-result policy may make an inconsistency acknowledgement-safe."""

        self._delivery()
        for error in (
            BasicPitchDatabaseUnavailable("PostgreSQL Basic Pitch task access is unavailable."),
            BasicPitchTaskClaimInconsistency("Basic Pitch request conflicts with durable state."),
        ):
            with self.subTest(error=type(error).__name__):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.side_effect = error
                self.channel.basic_ack.reset_mock()
                self.channel.basic_nack.reset_mock()

                with self.assertRaises(type(error)):
                    consume_one_basic_pitch_requested_delivery(
                        self.channel,
                        database=object(),  # type: ignore[arg-type]
                    )

                self.channel.basic_ack.assert_not_called()
                self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_claim_result_missing_lease_is_not_acknowledged(self, bridge) -> None:
        """A future regression cannot ack work while losing its durable ownership token."""

        self._delivery()
        bridge.return_value = BasicPitchDeliveryClaim(
            message=claimed_delivery(disposition=BasicPitchTaskClaimDisposition.CLAIMED).message,
            task_claim=BasicPitchTaskClaimResult(disposition=BasicPitchTaskClaimDisposition.CLAIMED),
        )

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            consume_one_basic_pitch_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_claim_result_missing_validated_message_is_not_acknowledged(self, bridge) -> None:
        """A claimed lease cannot proceed if its parser evidence was lost or malformed."""

        self._delivery()
        bridge.return_value = BasicPitchDeliveryClaim(
            message=object(),  # type: ignore[arg-type] - tests a bypassed dataclass constructor.
            task_claim=claimed_delivery(
                disposition=BasicPitchTaskClaimDisposition.CLAIMED
            ).task_claim,
        )

        with self.assertRaises(BasicPitchTaskLeaseProtocolError):
            consume_one_basic_pitch_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_acknowledgement_failure_is_retryable_without_nacking_the_committed_claim(self, bridge) -> None:
        """A lost ack produces duplicate-safe redelivery rather than a second claim here."""

        self._delivery()
        bridge.return_value = claimed_delivery(disposition=BasicPitchTaskClaimDisposition.DUPLICATE)
        self.channel.basic_ack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(BasicPitchAMQPUnavailable) as raised:
            consume_one_basic_pitch_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.assertEqual(str(raised.exception), "RabbitMQ Basic Pitch acknowledgement is unavailable.")
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_basic_pitch_requested_delivery")
    def test_rejection_failure_leaves_malformed_delivery_unacknowledged(self, bridge) -> None:
        """A failed nack is never permission to silently discard malformed evidence."""

        self._delivery()
        bridge.side_effect = BasicPitchRequestContractError("Basic Pitch request delivery is invalid.")
        self.channel.basic_nack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(BasicPitchAMQPUnavailable) as raised:
            consume_one_basic_pitch_requested_delivery(
                self.channel,
                database=object(),  # type: ignore[arg-type]
            )

        self.assertEqual(str(raised.exception), "RabbitMQ Basic Pitch rejection is unavailable.")
        self.channel.basic_ack.assert_not_called()


class BasicPitchConsumeOneResultTests(unittest.TestCase):
    """Prove outcome values cannot be paired with unsafe lease data."""

    def test_outcome_lease_pairing_is_checked_at_result_construction(self) -> None:
        """Only a successful broker acknowledgement can pass a lease onward."""

        lease = claimed_delivery(
            disposition=BasicPitchTaskClaimDisposition.CLAIMED
        ).task_claim.lease
        assert lease is not None
        message = claimed_delivery(
            disposition=BasicPitchTaskClaimDisposition.CLAIMED
        ).message
        for outcome, paired_lease, paired_message in (
            (BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE, None, None),
            (BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE, lease, None),
            (BasicPitchConsumeOneOutcome.ACKNOWLEDGED_LEASE, None, message),
            (BasicPitchConsumeOneOutcome.IDLE, lease, None),
            (BasicPitchConsumeOneOutcome.IDLE, None, message),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    BasicPitchConsumeOneResult(
                        outcome=outcome,
                        lease=paired_lease,
                        message=paired_message,
                    )


if __name__ == "__main__":
    unittest.main()
