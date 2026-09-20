"""Unit tests for one nested acknowledged Demucs runtime attempt.

Every lower boundary is replaced at its public context/function surface. These
tests make no model, MinIO, PostgreSQL, RabbitMQ, Docker, or Kubernetes call.
"""

from __future__ import annotations

import unittest
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.task_runtime_once import execute_acknowledged_demucs_task_once


class SourceClient:
    """Expose source-read methods so the runtime dependency preflight can pass."""

    def head_object(self, **_kwargs: object) -> object:
        """Never execute because the source workspace is patched in these tests."""

        raise AssertionError("The patched source workspace must own storage calls.")

    def get_object(self, **_kwargs: object) -> object:
        """Never execute because the source workspace is patched in these tests."""

        raise AssertionError("The patched source workspace must own storage calls.")


class ArtifactClient:
    """Expose PutObject so the runtime dependency preflight can pass."""

    def put_object(self, **_kwargs: object) -> object:
        """Never execute because the upload/commit composition is patched here."""

        raise AssertionError("The patched upload composition must own storage calls.")


class Database:
    """Expose short-transaction capability without opening a database connection."""

    def write_cursor(self) -> object:
        """Never execute because the start/completion boundaries are patched here."""

        raise AssertionError("The patched database boundaries must own transactions.")


def nested_context(
    events: list[str],
    name: str,
    value: object,
) -> AbstractContextManager[object]:
    """Return one recording context manager for a patched nested boundary."""

    @contextmanager
    def opened(*_args: object, **_kwargs: object) -> Iterator[object]:
        events.append(f"{name}-enter")
        try:
            yield value
        finally:
            events.append(f"{name}-exit")

    return opened()


class TaskRuntimeOnceTests(unittest.TestCase):
    """Prove one receive result follows the exact nested one-task order only."""

    @patch("app.task_runtime_once.upload_and_commit_demucs_stem_set")
    @patch("app.task_runtime_once.opened_demucs_stem_output_plan_workspace")
    @patch("app.task_runtime_once.opened_hashed_demucs_stem_inventory_workspace")
    @patch("app.task_runtime_once.opened_validated_demucs_stem_inventory_workspace")
    @patch("app.task_runtime_once.opened_executed_demucs_separation_workspace")
    @patch("app.task_runtime_once.opened_running_demucs_source_workspace")
    @patch("app.task_runtime_once.opened_acknowledged_demucs_source_workspace")
    def test_runs_one_acknowledged_task_in_order_then_unwinds_scopes(
        self,
        source_context,
        running_context,
        executed_context,
        validated_context,
        hashed_context,
        plans_context,
        upload_and_commit,
    ) -> None:
        """Completion sees all nested evidence only after source/model stages."""

        events: list[str] = []
        source = MagicMock(name="acknowledged-source-workspace")
        running = MagicMock(name="running-source-workspace")
        executed = MagicMock(name="executed-separation-workspace")
        validated = MagicMock(name="validated-stem-workspace")
        hashed = MagicMock(name="hashed-stem-workspace")
        plans = MagicMock(name="stem-plan-workspace")
        committed = MagicMock(name="committed-stem-set")
        source_context.side_effect = lambda *args, **kwargs: nested_context(events, "source", source)
        running_context.side_effect = lambda *args, **kwargs: nested_context(events, "running", running)
        executed_context.side_effect = lambda *args, **kwargs: nested_context(events, "executed", executed)
        validated_context.side_effect = lambda *args, **kwargs: nested_context(events, "validated", validated)
        hashed_context.side_effect = lambda *args, **kwargs: nested_context(events, "hashed", hashed)
        plans_context.side_effect = lambda *args, **kwargs: nested_context(events, "plans", plans)

        def record_upload_and_commit(**kwargs: object) -> object:
            """Record the final bridge after all local evidence scopes opened."""

            self.assertIs(kwargs["workspace"], plans)
            events.append("upload-and-commit")
            return committed

        upload_and_commit.side_effect = record_upload_and_commit
        result = MagicMock(name="acknowledged-lease-result")
        source_client = SourceClient()
        artifact_client = ArtifactClient()
        database = Database()
        work_directory = MagicMock(name="worker-scratch")
        uuid_factory = lambda: UUID("00000000-0000-4000-8000-000000000001")

        returned = execute_acknowledged_demucs_task_once(
            receive_result=result,  # type: ignore[arg-type]
            source_client=source_client,  # type: ignore[arg-type]
            artifact_client=artifact_client,  # type: ignore[arg-type]
            database=database,  # type: ignore[arg-type]
            work_directory=work_directory,
            event_id_factory=uuid_factory,
        )

        self.assertIs(returned, committed)
        self.assertEqual(
            events,
            [
                "source-enter",
                "running-enter",
                "executed-enter",
                "validated-enter",
                "hashed-enter",
                "plans-enter",
                "upload-and-commit",
                "plans-exit",
                "hashed-exit",
                "validated-exit",
                "executed-exit",
                "running-exit",
                "source-exit",
            ],
        )
        upload_and_commit.assert_called_once_with(
            workspace=plans,
            client=artifact_client,
            database=database,
            uploader=None,
            event_id_factory=uuid_factory,
        )
        executed_context.assert_called_once_with(
            running,
            work_directory=work_directory,
            runner=None,
            renewal_database=database,
        )

    @patch("app.task_runtime_once.opened_running_demucs_source_workspace")
    @patch("app.task_runtime_once.opened_acknowledged_demucs_source_workspace")
    def test_running_ownership_loss_stops_before_model_or_private_upload(
        self,
        source_context,
        running_context,
    ) -> None:
        """A committed no-row start result is a normal no-model stop signal."""

        events: list[str] = []
        source_context.side_effect = lambda *args, **kwargs: nested_context(
            events,
            "source",
            MagicMock(name="acknowledged-source-workspace"),
        )
        running_context.side_effect = lambda *args, **kwargs: nested_context(events, "running", None)

        returned = execute_acknowledged_demucs_task_once(
            receive_result=MagicMock(),  # type: ignore[arg-type]
            source_client=SourceClient(),  # type: ignore[arg-type]
            artifact_client=ArtifactClient(),  # type: ignore[arg-type]
            database=Database(),  # type: ignore[arg-type]
            work_directory=MagicMock(name="worker-scratch"),
        )

        self.assertIsNone(returned)
        self.assertEqual(events, ["source-enter", "running-enter", "running-exit", "source-exit"])

    @patch("app.task_runtime_once.opened_acknowledged_demucs_source_workspace")
    def test_missing_database_capability_stops_before_source_preflight(self, source_context) -> None:
        """An invalid runtime cannot download or process private audio first."""

        with self.assertRaises(TypeError):
            execute_acknowledged_demucs_task_once(
                receive_result=MagicMock(),  # type: ignore[arg-type]
                source_client=SourceClient(),  # type: ignore[arg-type]
                artifact_client=ArtifactClient(),  # type: ignore[arg-type]
                database=object(),  # type: ignore[arg-type]
                work_directory=MagicMock(name="worker-scratch"),
            )

        source_context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
