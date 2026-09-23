"""Offline tests for the read-only six-stem PostgreSQL state observer."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from lifecycle_handoff import (
    TemporaryPostgreSQLObserverCredentials,
    write_postgresql_observer_credentials,
)
from postgresql_durable_state_observer import (
    PostgreSQLDurableStateObserver,
    PostgreSQLDurableStateObserverError,
    observe_postgresql_durable_state_once,
)


_RUN_MARKER = "loadrun01"
_PASSWORD = "ObserverPasswordForOfflineTestOnly_123456"
_CREDENTIALS = TemporaryPostgreSQLObserverCredentials(
    run_marker=_RUN_MARKER,
    username=f"clouddsp_six_stem_observer_{_RUN_MARKER}",
    password=_PASSWORD,
    function_schema="public",
    function_name=f"clouddsp_six_stem_observe_{_RUN_MARKER}",
)


def aggregate_row() -> tuple[object, ...]:
    """Model counters and the exact three-job object evidence manifest."""

    artifacts = []
    for ordinal in range(1, 4):
        job_id = f"00000000-0000-0000-0000-{ordinal:012d}"
        filename = f"six-stem-load-{_RUN_MARKER}-{ordinal}.wav"
        stems = {
            name: {
                "bucket": "clouddsp-uploads",
                "object_key": f"stems/{job_id}/{name}.wav",
                "content_type": "audio/wav",
                "size_bytes": 1024,
                "sha256": f"{ordinal:x}" * 64,
            }
            for name in ("bass", "drums", "guitar", "other", "piano", "vocals")
        }
        artifacts.append({
            "job_id": job_id,
            "input_bucket": "clouddsp-uploads",
            "input_object_key": f"uploads/{job_id}/{filename}",
            "source_filename": filename,
            "source_content_type": "audio/wav",
            "source_size_bytes": 44,
            "source_sha256": f"{ordinal:x}" * 64,
            "load_ordinal": ordinal,
            "stem_mode": "6-stems",
            "stems": stems,
            "midi": {
                "drums": {
                    "status": "ready",
                    "extractor": "adtof",
                    "s3_key": f"midi/{job_id}/drums.mid",
                    "bpm_key": f"midi/{job_id}/drums_bpm.json",
                }
            },
        })
    return (
        3,
        3,
        {"completed": 3},
        3,
        15,
        3,
        0,
        0,
        {
            "demucs:succeeded": 3,
            "basic-pitch:succeeded": 15,
            "adtof:succeeded": 3,
        },
        {
            "demucs:demucs.requested:published": 3,
            "basic-pitch:basic-pitch.requested:published": 15,
            "adtof:adtof.requested:published": 3,
        },
        artifacts,
    )


class _FakeCursor:
    """Provide the narrow fetchall surface used by Psycopg's normal cursor."""

    def __init__(self, rows: object) -> None:
        self.rows = rows

    def fetchall(self) -> object:
        return self.rows


class _FakeConnection:
    """Record one query without opening a socket or contacting PostgreSQL."""

    def __init__(self, rows: object) -> None:
        self.rows = rows
        self.query: str | None = None
        self.closed = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.closed = True

    def execute(self, query: str) -> _FakeCursor:
        self.query = query
        return _FakeCursor(self.rows)


class PostgreSQLDurableStateObserverTests(unittest.TestCase):
    """Ensure the client reads one fixed aggregate and cannot widen its access."""

    def test_observes_one_aggregate_row_using_only_the_fixed_read_only_target(self) -> None:
        """The observer calls the marker-derived function and no table query."""

        connection = _FakeConnection([aggregate_row()])
        captured: dict[str, object] = {}

        def connect(**options: object) -> _FakeConnection:
            captured.update(options)
            return connection

        snapshot = PostgreSQLDurableStateObserver(
            credentials=_CREDENTIALS,
            connection_factory=connect,
        ).observe_once()

        self.assertEqual(snapshot.observed_job_count, 3)
        self.assertEqual(snapshot.source_uploaded_count, 3)
        self.assertEqual(snapshot.demucs_succeeded_count, 3)
        self.assertEqual(snapshot.basic_pitch_succeeded_count, 15)
        self.assertEqual(snapshot.adtof_succeeded_count, 3)
        self.assertEqual(snapshot.task_failure_count, 0)
        self.assertEqual(snapshot.active_task_lease_count, 0)
        self.assertEqual(snapshot.job_status_counts, (("completed", 3),))
        self.assertEqual(
            snapshot.task_status_counts,
            (("adtof:succeeded", 3), ("basic-pitch:succeeded", 15), ("demucs:succeeded", 3)),
        )
        self.assertEqual(
            snapshot.outbox_delivery_counts,
            (
                ("adtof:adtof.requested:published", 3),
                ("basic-pitch:basic-pitch.requested:published", 15),
                ("demucs:demucs.requested:published", 3),
            ),
        )
        self.assertEqual(len(snapshot.artifact_evidence), 3)
        self.assertEqual(len(snapshot.artifact_evidence[0].stems), 6)
        self.assertEqual(
            connection.query,
            'SELECT observed_job_count, source_uploaded_count, job_status_counts, '
            'demucs_succeeded_count, basic_pitch_succeeded_count, adtof_succeeded_count, '
            'task_failure_count, active_task_lease_count, task_status_counts, '
            'outbox_delivery_counts, artifact_evidence FROM "public"."clouddsp_six_stem_observe_loadrun01"()',
        )
        self.assertEqual(captured["host"], "clouddsp-postgresql.clouddsp-data.svc")
        self.assertEqual(captured["port"], 5432)
        self.assertEqual(captured["dbname"], "clouddsp_job_api")
        self.assertEqual(captured["user"], _CREDENTIALS.username)
        self.assertEqual(captured["password"], _PASSWORD)
        self.assertEqual(captured["connect_timeout"], 5)
        self.assertEqual(
            captured["options"], "-c default_transaction_read_only=on"
        )
        self.assertTrue(connection.closed)
        self.assertNotIn(_PASSWORD, repr(snapshot))

    def test_reads_credentials_only_from_the_separate_observer_handoff(self) -> None:
        """The public helper accepts the restricted credential-volume path."""

        connection = _FakeConnection([aggregate_row()])
        captured: dict[str, object] = {}

        def connect(**options: object) -> _FakeConnection:
            captured.update(options)
            return connection

        with tempfile.TemporaryDirectory() as temporary_directory:
            credential_directory = Path(temporary_directory)
            credential_directory.mkdir(exist_ok=True)
            write_postgresql_observer_credentials(
                credential_directory,
                credentials=_CREDENTIALS,
            )

            snapshot = observe_postgresql_durable_state_once(
                credential_directory=credential_directory,
                connection_factory=connect,
            )

        self.assertEqual(snapshot.observed_job_count, 3)
        self.assertEqual(captured["user"], _CREDENTIALS.username)
        self.assertIn(
            'FROM "public"."clouddsp_six_stem_observe_loadrun01"()',
            connection.query or "",
        )

    def test_rejects_a_hand_built_identifier_before_opening_a_connection(self) -> None:
        """A changed schema or function name cannot inject arbitrary SQL."""

        called = False

        def connect(**_options: object) -> _FakeConnection:
            nonlocal called
            called = True
            return _FakeConnection([aggregate_row()])

        for credentials in (
            replace(_CREDENTIALS, function_schema="private"),
            replace(_CREDENTIALS, function_name="observer(); SELECT * FROM jobs --"),
            replace(_CREDENTIALS, username="clouddsp-job-api"),
        ):
            with self.subTest(credentials=credentials.function_schema):
                with self.assertRaisesRegex(PostgreSQLDurableStateObserverError, "contract"):
                    PostgreSQLDurableStateObserver(
                        credentials=credentials,
                        connection_factory=connect,
                    )
        self.assertFalse(called)

    def test_rejects_missing_extra_or_malformed_aggregate_output(self) -> None:
        """Unexpected database return shapes never become a passing snapshot."""

        malformed_rows = (
            [],
            [aggregate_row(), aggregate_row()],
            [(3,)],
            [(True, *aggregate_row()[1:])],
            [(3, 3, {"completed": True}, *aggregate_row()[3:])],
            [(3, 3, {"completed": 3}, 3, 15, 3, 0, 0, {"bad key": 3}, {})],
        )
        for rows in malformed_rows:
            with self.subTest(row_count=len(rows)):
                with self.assertRaises(PostgreSQLDurableStateObserverError):
                    PostgreSQLDurableStateObserver(
                        credentials=_CREDENTIALS,
                        connection_factory=lambda **_options: _FakeConnection(rows),
                    ).observe_once()

    def test_suppresses_driver_diagnostics_that_might_contain_credentials(self) -> None:
        """Database exception text and connection secrets never escape the adapter."""

        def failing_connect(**_options: object) -> _FakeConnection:
            raise RuntimeError(f"host detail password={_PASSWORD}")

        with self.assertRaises(PostgreSQLDurableStateObserverError) as raised:
            PostgreSQLDurableStateObserver(
                credentials=_CREDENTIALS,
                connection_factory=failing_connect,
            ).observe_once()
        self.assertNotIn(_PASSWORD, str(raised.exception))


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
