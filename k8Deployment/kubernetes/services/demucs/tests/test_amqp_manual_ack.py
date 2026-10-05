"""Unit tests for one Demucs Pika-shaped manual-ack receive attempt.

All channels and bridge calls are mocks.  These tests open no RabbitMQ or
PostgreSQL connection, do not run a consumer loop, and do not touch MinIO,
audio, Docker, or Kubernetes.  They verify the crucial difference between a
committed durable result, a permanent malformed contract, and a retryable
dependency failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.messaging.amqp_manual_ack import (
    DEMUCS_REQUEST_QUEUE,
    DemucsAMQPUnavailable,
    DemucsConsumeOneOutcome,
    DemucsConsumeOneResult,
    consume_one_demucs_requested_delivery,
)
from app.runtime.delivery_claim import DemucsDeliveryClaim
from app.messaging.demucs_requested_message import DemucsRequestContractError, DemucsRequestedMessage
from app.db.postgresql import DemucsDatabaseUnavailable
from app.db.task_lease import (
    DemucsStaleRequestReason,
    DemucsTaskClaimDisposition,
    DemucsTaskClaimInconsistency,
    DemucsTaskLease,
    DemucsTaskLeaseProtocolError,
    DemucsTaskClaimResult,
)


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
TASK_ID = "c21a7035-6ee2-495c-8f77-a2d2b8d33a8d"
LEASE_TOKEN = "49d78c86-9591-4fcb-85d8-694c15808a65"


def claimed_delivery(*, disposition: DemucsTaskClaimDisposition) -> DemucsDeliveryClaim:
    """Return a committed bridge result without connecting to either service."""

    claim_kwargs: dict[str, object] = {"disposition": disposition}
    if disposition is DemucsTaskClaimDisposition.CLAIMED:
        # A true claimed result includes the exact lease token/expiry the
        # future worker must own before it starts source or model work.
        claim_kwargs["lease"] = DemucsTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"uploads/{JOB_ID}/mix.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=datetime(2026, 9, 8, 12, 15, tzinfo=UTC),
        )
    elif disposition is DemucsTaskClaimDisposition.DUPLICATE:
        claim_kwargs["duplicate_status"] = "running"
    elif disposition is DemucsTaskClaimDisposition.STALE:
        claim_kwargs["stale_reason"] = DemucsStaleRequestReason.JOB_EXPIRED
    return DemucsDeliveryClaim(
        message=DemucsRequestedMessage(
            event_id=EVENT_ID,
            job_id=JOB_ID,
            source_bucket="clouddsp-uploads",
            source_object_key=f"uploads/{JOB_ID}/mix.wav",
            stem_mode="4-stems",
        ),
        task_claim=DemucsTaskClaimResult(**claim_kwargs),  # type: ignore[arg-type]
    )


class ManualAcknowledgementTests(unittest.TestCase):
    """Prove only durable-safe claim outcomes remove a normal request delivery."""

    def setUp(self) -> None:
        """Give every test one Pika-shaped channel and request frame."""

        self.channel = MagicMock()
        self.method = SimpleNamespace(
            exchange="clouddsp.processing-events",
            routing_key="demucs.requested",
            delivery_tag=42,
        )

    def _delivery(self, *, body: object = b"{}") -> None:
        """Make one normal `basic_get` call return this fake broker delivery."""

        self.channel.basic_get.return_value = (self.method, SimpleNamespace(), body)

    def test_empty_queue_is_idle_without_bridge_or_broker_action(self) -> None:
        """A later supervisor can sleep only when no unacknowledged work exists."""

        self.channel.basic_get.return_value = (None, None, None)
        database = object()

        with patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery") as bridge:
            result = consume_one_demucs_requested_delivery(self.channel, database=database)  # type: ignore[arg-type]

        self.assertEqual(result.outcome, DemucsConsumeOneOutcome.IDLE)
        self.assertIsNone(result.lease)
        self.channel.basic_get.assert_called_once_with(queue=DEMUCS_REQUEST_QUEUE, auto_ack=False)
        bridge.assert_not_called()
        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_claimed_lease_is_preserved_only_after_ack_while_duplicate_stale_need_no_work(self, bridge) -> None:
        """The future runtime receives a lease only when this call owns work."""

        self._delivery()
        database = object()
        for disposition in DemucsTaskClaimDisposition:
            with self.subTest(disposition=disposition):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.return_value = claimed_delivery(disposition=disposition)
                self.channel.basic_ack.reset_mock()

                result = consume_one_demucs_requested_delivery(self.channel, database=database)  # type: ignore[arg-type]

                expected_delivery = claimed_delivery(disposition=disposition)
                expected_lease = expected_delivery.task_claim.lease
                self.assertEqual(
                    result.outcome,
                    (
                        DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE
                        if disposition is DemucsTaskClaimDisposition.CLAIMED
                        else DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK
                    ),
                )
                self.assertEqual(result.lease, expected_lease)
                bridge.assert_called_once_with(
                    database=database,
                    delivery_exchange="clouddsp.processing-events",
                    delivery_routing_key="demucs.requested",
                    properties=self.channel.basic_get.return_value[1],
                    body=b"{}",
                )
                self.channel.basic_ack.assert_called_once_with(42)
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_malformed_contract_is_nacked_without_requeue_and_never_acknowledged(self, bridge) -> None:
        """Permanent parser faults use the configured main-queue dead-letter path."""

        self._delivery(body=b"not-json")
        bridge.side_effect = DemucsRequestContractError("Demucs request delivery is invalid.")

        result = consume_one_demucs_requested_delivery(self.channel, database=object())  # type: ignore[arg-type]

        self.assertEqual(result.outcome, DemucsConsumeOneOutcome.MALFORMED_REJECTED)
        self.assertIsNone(result.lease)
        self.channel.basic_nack.assert_called_once_with(42, requeue=False)
        self.channel.basic_ack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_database_or_identity_failure_remains_unacknowledged_for_later_policy(self, bridge) -> None:
        """Only a terminal-transition task may turn a durable inconsistency into an ack."""

        self._delivery()
        for error in (
            DemucsDatabaseUnavailable("PostgreSQL Demucs task access is unavailable."),
            DemucsTaskClaimInconsistency("Demucs request conflicts with durable state."),
        ):
            with self.subTest(error=type(error).__name__):
                bridge.reset_mock(return_value=True, side_effect=True)
                bridge.side_effect = error
                self.channel.basic_ack.reset_mock()
                self.channel.basic_nack.reset_mock()

                with self.assertRaises(type(error)):
                    consume_one_demucs_requested_delivery(self.channel, database=object())  # type: ignore[arg-type]

                self.channel.basic_ack.assert_not_called()
                self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_claim_result_missing_its_lease_is_not_acknowledged(self, bridge) -> None:
        """A future regression cannot acknowledge work without returning its owner token."""

        self._delivery()
        bridge.return_value = DemucsDeliveryClaim(
            message=claimed_delivery(disposition=DemucsTaskClaimDisposition.CLAIMED).message,
            task_claim=DemucsTaskClaimResult(disposition=DemucsTaskClaimDisposition.CLAIMED),
        )

        with self.assertRaises(DemucsTaskLeaseProtocolError):
            consume_one_demucs_requested_delivery(self.channel, database=object())  # type: ignore[arg-type]

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_acknowledgement_failure_is_retryable_and_never_replaces_durable_claim(self, bridge) -> None:
        """A lost ack causes duplicate-safe redelivery rather than a second claim attempt here."""

        self._delivery()
        bridge.return_value = claimed_delivery(disposition=DemucsTaskClaimDisposition.DUPLICATE)
        self.channel.basic_ack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(DemucsAMQPUnavailable) as raised:
            consume_one_demucs_requested_delivery(self.channel, database=object())  # type: ignore[arg-type]

        self.assertEqual(str(raised.exception), "RabbitMQ Demucs acknowledgement is unavailable.")
        self.channel.basic_nack.assert_not_called()

    @patch("app.messaging.amqp_manual_ack.claim_demucs_requested_delivery")
    def test_rejection_failure_leaves_malformed_delivery_unacknowledged(self, bridge) -> None:
        """A failed nack is not permission to silently discard malformed evidence."""

        self._delivery()
        bridge.side_effect = DemucsRequestContractError("Demucs request delivery is invalid.")
        self.channel.basic_nack.side_effect = RuntimeError("private channel diagnostic")

        with self.assertRaises(DemucsAMQPUnavailable) as raised:
            consume_one_demucs_requested_delivery(self.channel, database=object())  # type: ignore[arg-type]

        self.assertEqual(str(raised.exception), "RabbitMQ Demucs rejection is unavailable.")
        self.channel.basic_ack.assert_not_called()


class DemucsConsumeOneResultTests(unittest.TestCase):
    """Prove outcome values cannot be paired with an unsafe lease shape."""

    def test_outcome_lease_pairing_is_checked_at_result_construction(self) -> None:
        """Only a successfully acknowledged first lease may reach preflight code."""

        lease = claimed_delivery(disposition=DemucsTaskClaimDisposition.CLAIMED).task_claim.lease
        assert lease is not None
        for outcome, paired_lease in (
            (DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE, None),
            (DemucsConsumeOneOutcome.IDLE, lease),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    DemucsConsumeOneResult(outcome=outcome, lease=paired_lease)


if __name__ == "__main__":
    unittest.main()
