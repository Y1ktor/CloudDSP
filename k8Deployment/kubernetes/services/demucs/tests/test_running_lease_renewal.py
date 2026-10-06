"""Tests for the one-shot committed Demucs running-lease renewal adapter.

The existing short transaction wrapper is patched at its public seam. These
tests prove result shaping and exact token forwarding only; they open no
PostgreSQL/MinIO/RabbitMQ connection, run no Demucs process, wait on no timer,
and make no Docker/Kubernetes change.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.db.preflight_task_start import DemucsRunningSource
from app.db.running_lease_renewal import (
    DemucsRunningLeaseRenewal,
    DemucsRunningLeaseRenewalOutcome,
    renew_running_demucs_lease,
)
from app.processing.source_preflight import ValidatedDemucsSource
from app.db.task_lease import DemucsTaskLease


TASK_ID = "00000000-0000-4000-8000-000000000001"
JOB_ID = "00000000-0000-4000-8000-000000000002"
EVENT_ID = "00000000-0000-4000-8000-000000000003"
LEASE_TOKEN = "00000000-0000-4000-8000-000000000004"
INITIAL_EXPIRY = datetime(2026, 9, 20, 12, 15, tzinfo=UTC)
RENEWED_EXPIRY = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)


def running_source() -> DemucsRunningSource:
    """Build a real committed-running value with inert source evidence."""

    return DemucsRunningSource(
        lease=DemucsTaskLease(
            task_id=TASK_ID,
            job_id=JOB_ID,
            request_event_id=EVENT_ID,
            input_bucket="clouddsp-uploads",
            input_object_key=f"uploads/{JOB_ID}/mix.wav",
            stem_mode="4-stems",
            attempt_count=1,
            lease_token=LEASE_TOKEN,
            lease_expires_at=INITIAL_EXPIRY,
        ),
        source=ValidatedDemucsSource(
            source_object=MagicMock(),
            audio_probe=MagicMock(),
        ),
        started_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    )


class DemucsRunningLeaseRenewalTests(unittest.TestCase):
    """Prove renewal changes only expiry and reports no-row loss explicitly."""

    @patch("app.db.running_lease_renewal.renew_one_demucs_task_lease")
    def test_committed_renewal_preserves_source_and_identity_with_new_expiry(self, renew) -> None:
        """Only PostgreSQL's returned expiry changes after a guarded renewal."""

        running = running_source()
        database = MagicMock()
        renew.return_value = RENEWED_EXPIRY

        result = renew_running_demucs_lease(
            database=database,  # type: ignore[arg-type]
            running=running,
            lease_seconds=123,
        )

        self.assertEqual(result.outcome, DemucsRunningLeaseRenewalOutcome.RENEWED)
        assert result.running is not None
        self.assertEqual(result.running.lease.lease_expires_at, RENEWED_EXPIRY)
        self.assertEqual(result.running.lease.task_id, TASK_ID)
        self.assertEqual(result.running.lease.lease_token, LEASE_TOKEN)
        self.assertIs(result.running.source, running.source)
        self.assertEqual(result.running.started_at, running.started_at)
        renew.assert_called_once_with(
            database=database,
            task_id=TASK_ID,
            lease_token=LEASE_TOKEN,
            lease_seconds=123,
        )

    @patch("app.db.running_lease_renewal.renew_one_demucs_task_lease")
    def test_no_row_is_a_stop_signal_without_forged_running_evidence(self, renew) -> None:
        """An expired/recovered/inactive task must stop before another write."""

        database = MagicMock()
        renew.return_value = None

        result = renew_running_demucs_lease(
            database=database,  # type: ignore[arg-type]
            running=running_source(),
        )

        self.assertEqual(result.outcome, DemucsRunningLeaseRenewalOutcome.OWNERSHIP_LOST)
        self.assertIsNone(result.running)

    @patch("app.db.running_lease_renewal.renew_one_demucs_task_lease")
    def test_invalid_inputs_fail_before_the_renewal_transaction(self, renew) -> None:
        """A hand-built state or missing transaction capability has no side effect."""

        with self.assertRaisesRegex(TypeError, "running must be DemucsRunningSource"):
            renew_running_demucs_lease(
                database=MagicMock(),  # type: ignore[arg-type]
                running=object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "database must provide write_cursor"):
            renew_running_demucs_lease(
                database=object(),  # type: ignore[arg-type]
                running=running_source(),
            )

        renew.assert_not_called()

    @patch("app.db.running_lease_renewal.renew_one_demucs_task_lease")
    def test_database_failure_propagates_without_inventing_renewal_evidence(self, renew) -> None:
        """An uncertain transaction must not become renewed or ownership-lost state."""

        failure = RuntimeError("private database failure")
        renew.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            renew_running_demucs_lease(
                database=MagicMock(),  # type: ignore[arg-type]
                running=running_source(),
            )

        self.assertIs(raised.exception, failure)


class DemucsRunningLeaseRenewalResultTests(unittest.TestCase):
    """Prove renewal result states cannot retain contradictory authority."""

    def test_outcome_and_running_evidence_pairing_is_checked(self) -> None:
        """Only a confirmed renewal may expose an updated running source."""

        running = running_source()
        for outcome, paired_running in (
            (DemucsRunningLeaseRenewalOutcome.RENEWED, None),
            (DemucsRunningLeaseRenewalOutcome.OWNERSHIP_LOST, running),
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    DemucsRunningLeaseRenewal(
                        outcome=outcome,
                        running=paired_running,
                    )


if __name__ == "__main__":
    unittest.main()
