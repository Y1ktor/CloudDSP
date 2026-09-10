"""Unit tests for generic leased-outbox route selection.

The selector and both contract builders are pure Python.  These tests do not
import Pika lazily, open a PostgreSQL transaction, connect to RabbitMQ, publish
a message, access MinIO, or create a Kubernetes resource.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from app.dispatchable_outbox_request import (
    DispatchableOutboxRequestContractError,
    build_dispatchable_amqp_request,
)
from app.outbox_lease import LeasedOutboxEvent


EVENT_ID = "93b31df9-ea8c-46bb-b2c0-19e9db5365d5"
JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"
LEASE_TOKEN = "0a2e7458-c355-4933-ac0b-5788eecc504d"
LEASE_EXPIRY = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def leased_event(
    *,
    stage: str,
    stem_name: str,
    event_type: str,
    payload: object,
) -> LeasedOutboxEvent:
    """Build a lease record shaped like the generic SQL adapter's return value."""

    # Tests intentionally construct a frozen record rather than calling the
    # database claim helper: route selection must remain isolated from SQL.
    return LeasedOutboxEvent(
        event_id=EVENT_ID,
        job_id=JOB_ID,
        stage=stage,
        stem_name=stem_name,
        event_type=event_type,
        payload=payload,  # type: ignore[arg-type]
        delivery_attempts=1,
        lease_token=LEASE_TOKEN,
        lease_expires_at=LEASE_EXPIRY,
    )


def demucs_payload() -> dict[str, object]:
    """Return the exact version-1 private-source contract for Demucs."""

    return {
        "schema_version": 1,
        "job_id": JOB_ID,
        "source": {
            "bucket": "clouddsp-uploads",
            "object_key": f"uploads/{JOB_ID}/mix.wav",
        },
        "stem_mode": "4-stems",
    }


def downstream_payload(*, stem_name: str) -> dict[str, object]:
    """Return the exact version-1 private-WAV contract for one stem event."""

    return {
        "schema_version": 1,
        "job_id": JOB_ID,
        "stem_name": stem_name,
        "stem": {
            "bucket": "clouddsp-uploads",
            "object_key": f"stems/{JOB_ID}/{stem_name}.wav",
            "content_type": "audio/wav",
            "size_bytes": 1234,
            "sha256": "a" * 64,
        },
    }


class DispatchableOutboxRequestTests(unittest.TestCase):
    """Prove each finite durable triple reaches only its reviewed AMQP route."""

    def test_demucs_event_uses_the_existing_private_source_contract(self) -> None:
        """The selector preserves the existing first-stage exchange/metadata."""

        request = build_dispatchable_amqp_request(
            leased_event(
                stage="demucs",
                stem_name="",
                event_type="demucs.requested",
                payload=demucs_payload(),
            )
        )

        self.assertEqual(request.exchange, "clouddsp.processing-events")
        self.assertEqual(request.routing_key, "demucs.requested")
        self.assertEqual(request.message_type, "demucs.requested")
        self.assertEqual(json.loads(request.body.decode("utf-8")), demucs_payload())

    def test_pitched_stem_uses_the_basic_pitch_contract(self) -> None:
        """A non-drum output cannot accidentally take the Demucs route."""

        request = build_dispatchable_amqp_request(
            leased_event(
                stage="basic-pitch",
                stem_name="bass",
                event_type="basic-pitch.requested",
                payload=downstream_payload(stem_name="bass"),
            )
        )

        self.assertEqual(request.routing_key, "basic-pitch.requested")
        self.assertEqual(request.message_type, "basic-pitch.requested")

    def test_drum_stem_uses_the_adtof_contract(self) -> None:
        """Drums are routed only to ADTOF through its fixed contract."""

        request = build_dispatchable_amqp_request(
            leased_event(
                stage="adtof",
                stem_name="drums",
                event_type="adtof.requested",
                payload=downstream_payload(stem_name="drums"),
            )
        )

        self.assertEqual(request.routing_key, "adtof.requested")
        self.assertEqual(request.message_type, "adtof.requested")

    def test_crossed_tuple_or_wrong_payload_becomes_one_safe_error_category(self) -> None:
        """The selector never falls back to a guessed route or raw exception."""

        invalid_events = (
            leased_event(
                stage="basic-pitch",
                stem_name="drums",
                event_type="basic-pitch.requested",
                payload=downstream_payload(stem_name="drums"),
            ),
            leased_event(
                stage="demucs",
                stem_name="",
                event_type="demucs.requested",
                payload=downstream_payload(stem_name="bass"),
            ),
        )
        for event in invalid_events:
            with self.subTest(stage=event.stage, stem_name=event.stem_name):
                with self.assertRaises(DispatchableOutboxRequestContractError) as raised:
                    build_dispatchable_amqp_request(event)
                self.assertEqual(str(raised.exception), "Dispatcher outbox event contract is invalid.")

    def test_untyped_value_is_rejected_before_any_contract_builder_runs(self) -> None:
        """Only the validated generic lease data model may enter this boundary."""

        with self.assertRaises(DispatchableOutboxRequestContractError):
            build_dispatchable_amqp_request(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
