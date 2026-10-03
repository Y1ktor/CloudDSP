"""Unit tests for the acknowledged-lease-to-source-preflight handoff.

The real MinIO/FFprobe composition is patched at its public boundary. These
tests prove receive outcomes control whether it may run; they never contact
RabbitMQ, PostgreSQL, MinIO, FFprobe, a Demucs model, Docker, or Kubernetes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from app.runtime.acknowledged_lease_preflight import (
    DemucsAcknowledgedLeasePreflightError,
    opened_acknowledged_demucs_source_workspace,
    preflight_acknowledged_demucs_lease,
)
from app.messaging.amqp_manual_ack import DemucsConsumeOneOutcome, DemucsConsumeOneResult
from app.processing.source_preflight import OpenedValidatedDemucsSourceWorkspace, ValidatedDemucsSource
from app.db.task_lease import DemucsTaskLease


JOB_ID = "08ec1d44-3106-4fcb-91c8-5d0c78e7e046"


def acknowledged_lease() -> DemucsTaskLease:
    """Return the one durable lease that an acknowledged claim may hand off."""

    return DemucsTaskLease(
        task_id="c21a7035-6ee2-495c-8f77-a2d2b8d33a8d",
        job_id=JOB_ID,
        request_event_id="93b31df9-ea8c-46bb-b2c0-19e9db5365d5",
        input_bucket="clouddsp-uploads",
        input_object_key=f"uploads/{JOB_ID}/mix.wav",
        stem_mode="4-stems",
        attempt_count=1,
        lease_token="49d78c86-9591-4fcb-85d8-694c15808a65",
        lease_expires_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


class AcknowledgedLeasePreflightTests(unittest.TestCase):
    """Prove only a successful broker/durable lease handoff starts source work."""

    @patch("app.runtime.acknowledged_lease_preflight.validate_claimed_demucs_source")
    def test_acknowledged_lease_forwards_the_exact_token_to_source_preflight(self, preflight) -> None:
        """The next layer retains both ownership evidence and source evidence."""

        lease = acknowledged_lease()
        result = DemucsConsumeOneResult(
            outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
            lease=lease,
        )
        client = object()
        runner = object()
        validated = MagicMock(spec=ValidatedDemucsSource)
        preflight.return_value = validated

        handoff = preflight_acknowledged_demucs_lease(
            result,
            client,  # type: ignore[arg-type]
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=runner,  # type: ignore[arg-type]
        )

        self.assertIs(handoff.lease, lease)
        self.assertIs(handoff.source, validated)
        preflight.assert_called_once_with(
            client,
            lease=lease,
            work_directory=Path("/worker-scratch"),
            ffprobe_runner=runner,
        )

    @patch("app.runtime.acknowledged_lease_preflight.validate_claimed_demucs_source")
    def test_all_other_normal_receive_outcomes_stop_before_storage_or_ffprobe(self, preflight) -> None:
        """No-work outcomes cannot accidentally restart historical audio work."""

        for outcome in (
            DemucsConsumeOneOutcome.IDLE,
            DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK,
            DemucsConsumeOneOutcome.MALFORMED_REJECTED,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(DemucsAcknowledgedLeasePreflightError) as raised:
                    preflight_acknowledged_demucs_lease(
                        DemucsConsumeOneResult(outcome=outcome),
                        object(),  # type: ignore[arg-type]
                        work_directory=Path("/worker-scratch"),
                    )

                self.assertEqual(
                    str(raised.exception),
                    "Demucs source preflight requires an acknowledged task lease.",
                )

        preflight.assert_not_called()

    @patch("app.runtime.acknowledged_lease_preflight.validate_claimed_demucs_source")
    def test_source_preflight_failure_propagates_without_a_new_runtime_policy(self, preflight) -> None:
        """The later token-guarded result adapter must classify source failures."""

        result = DemucsConsumeOneResult(
            outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
            lease=acknowledged_lease(),
        )
        failure = RuntimeError("private fake storage diagnostic")
        preflight.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            preflight_acknowledged_demucs_lease(
                result,
                object(),  # type: ignore[arg-type]
                work_directory=Path("/worker-scratch"),
            )

        self.assertIs(raised.exception, failure)

    def test_workspace_retains_a_path_only_for_an_acknowledged_lease(self) -> None:
        """A future `leased -> running` transaction can receive evidence without a path."""

        lease = acknowledged_lease()
        source = MagicMock(spec=ValidatedDemucsSource)
        local_path = Path("/worker-scratch/demucs-source-random/source.media")
        calls: list[tuple[object, object, object, object]] = []

        @contextmanager
        def opened_workspace(
            client: object,
            *,
            lease: DemucsTaskLease,
            work_directory: Path,
            ffprobe_runner: object | None = None,
        ) -> Iterator[OpenedValidatedDemucsSourceWorkspace]:
            calls.append((client, lease, work_directory, ffprobe_runner))
            yield OpenedValidatedDemucsSourceWorkspace(
                validated_source=source,
                source_path=local_path,
            )

        result = DemucsConsumeOneResult(
            outcome=DemucsConsumeOneOutcome.ACKNOWLEDGED_LEASE,
            lease=lease,
        )
        client = object()
        runner = object()
        with patch(
            "app.runtime.acknowledged_lease_preflight.opened_validated_demucs_source_workspace",
            opened_workspace,
        ):
            with opened_acknowledged_demucs_source_workspace(
                result,
                client,  # type: ignore[arg-type]
                work_directory=Path("/worker-scratch"),
                ffprobe_runner=runner,  # type: ignore[arg-type]
            ) as workspace:
                self.assertIs(workspace.lease, lease)
                self.assertIs(workspace.source, source)
                self.assertEqual(workspace.source_path, local_path)
                self.assertIs(workspace.preflight.lease, lease)
                self.assertIs(workspace.preflight.source, source)

        self.assertEqual(calls, [(client, lease, Path("/worker-scratch"), runner)])

    @patch("app.runtime.acknowledged_lease_preflight.opened_validated_demucs_source_workspace")
    def test_workspace_rejects_every_no_work_outcome_before_opening_storage(self, opened_workspace) -> None:
        """The temporary-file capability is not available for historic deliveries."""

        for outcome in (
            DemucsConsumeOneOutcome.IDLE,
            DemucsConsumeOneOutcome.ACKNOWLEDGED_NO_WORK,
            DemucsConsumeOneOutcome.MALFORMED_REJECTED,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(DemucsAcknowledgedLeasePreflightError):
                    with opened_acknowledged_demucs_source_workspace(
                        DemucsConsumeOneResult(outcome=outcome),
                        object(),  # type: ignore[arg-type]
                        work_directory=Path("/worker-scratch"),
                    ):
                        self.fail("A no-work receive outcome must not open a source workspace.")

        opened_workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
