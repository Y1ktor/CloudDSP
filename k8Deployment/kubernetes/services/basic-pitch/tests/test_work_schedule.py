"""Unit tests for the pure Basic Pitch broker/retry fair-work policy.

There is no RabbitMQ, PostgreSQL, MinIO, Basic Pitch, Docker, or Kubernetes
interaction here. The policy tests only prove the local source order a later
bounded runtime must follow.
"""

from __future__ import annotations

import unittest

from app.runtime.work_schedule import (
    BasicPitchWorkScheduleState,
    BasicPitchWorkSelection,
    BasicPitchWorkSource,
    select_next_basic_pitch_work_source,
)


class BasicPitchWorkScheduleTests(unittest.TestCase):
    """Prove neither normal delivery nor durable recovery can be starved."""

    def test_default_state_alternates_broker_and_due_retry_attempts(self) -> None:
        """Backlog on either source receives every second bounded selection."""

        first = select_next_basic_pitch_work_source(BasicPitchWorkScheduleState())
        second = select_next_basic_pitch_work_source(first.next_state)
        third = select_next_basic_pitch_work_source(second.next_state)
        fourth = select_next_basic_pitch_work_source(third.next_state)

        self.assertEqual(
            [first.source, second.source, third.source, fourth.source],
            [
                BasicPitchWorkSource.RABBITMQ_DELIVERY,
                BasicPitchWorkSource.DUE_RETRY_RECOVERY,
                BasicPitchWorkSource.RABBITMQ_DELIVERY,
                BasicPitchWorkSource.DUE_RETRY_RECOVERY,
            ],
        )

    def test_empty_preferred_source_has_an_immediate_other_source_fallback(self) -> None:
        """The later runtime can check a due retry before taking an idle nap."""

        broker_attempt = select_next_basic_pitch_work_source(BasicPitchWorkScheduleState())
        # Imagine this RabbitMQ attempt returns its normal `IDLE` result. The
        # policy itself never polls, but its advanced state selects recovery
        # immediately; only a second idle attempt merits supervisor waiting.
        recovery_attempt = select_next_basic_pitch_work_source(broker_attempt.next_state)

        self.assertEqual(broker_attempt.source, BasicPitchWorkSource.RABBITMQ_DELIVERY)
        self.assertEqual(recovery_attempt.source, BasicPitchWorkSource.DUE_RETRY_RECOVERY)
        self.assertEqual(
            recovery_attempt.next_state.next_source,
            BasicPitchWorkSource.RABBITMQ_DELIVERY,
        )

    def test_direct_construction_cannot_disable_source_alternation(self) -> None:
        """A future runtime cannot forge a selection that repeatedly polls one side."""

        with self.assertRaises(ValueError):
            BasicPitchWorkSelection(
                source=BasicPitchWorkSource.RABBITMQ_DELIVERY,
                next_state=BasicPitchWorkScheduleState(
                    next_source=BasicPitchWorkSource.RABBITMQ_DELIVERY,
                ),
            )

    def test_invalid_state_or_selection_values_are_rejected_before_any_runtime_io(self) -> None:
        """Strings cannot redirect the policy to an unreviewed work source."""

        with self.assertRaises(TypeError):
            BasicPitchWorkScheduleState(next_source="rabbitmq_delivery")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            select_next_basic_pitch_work_source(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
