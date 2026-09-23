"""Offline checks for exact load shape and bounded KEDA scale evidence."""

from __future__ import annotations

import unittest

from keda_scale_observer import KEDAScaleSnapshot, WorkerScaleState
from postgresql_durable_state_observer import snapshot_from_aggregate_row
from six_stem_load_observer import (
    _ScaleEvidence,
    _database_pipeline_succeeded,
    _has_terminal_failure,
)
from test_postgresql_durable_state_observer import aggregate_row, _RUN_MARKER


class SixStemLoadObserverTests(unittest.TestCase):
    """Only exact database counters and bounded worker replicas can pass."""

    def test_requires_exact_three_job_downstream_completion(self) -> None:
        snapshot = snapshot_from_aggregate_row(aggregate_row(), run_marker=_RUN_MARKER)
        self.assertTrue(_database_pipeline_succeeded(snapshot))
        self.assertFalse(_has_terminal_failure(snapshot))

        failed_row = list(aggregate_row())
        failed_row[2] = {"failed": 1, "completed": 2}
        failed_snapshot = snapshot_from_aggregate_row(tuple(failed_row), run_marker=_RUN_MARKER)
        self.assertTrue(_has_terminal_failure(failed_snapshot))
        self.assertFalse(_database_pipeline_succeeded(failed_snapshot))

    def test_records_peak_replicas_but_rejects_a_scale_above_the_cap(self) -> None:
        evidence = _ScaleEvidence()
        snapshot = KEDAScaleSnapshot(workers=(
            WorkerScaleState("demucs", 1, 1, 1, 1, 1, True, True),
            WorkerScaleState("basic-pitch", 3, 2, 3, 3, 3, True, True),
            WorkerScaleState("adtof", 2, 2, 2, 2, 2, True, True),
        ))
        evidence.add(snapshot)
        self.assertEqual(evidence.peaks, {"demucs": 1, "basic-pitch": 3, "adtof": 2})

        over_cap = KEDAScaleSnapshot(workers=(
            WorkerScaleState("demucs", 1, 1, 1, 1, 1, True, True),
            WorkerScaleState("basic-pitch", 4, 4, 4, 4, 3, True, True),
            WorkerScaleState("adtof", 1, 1, 1, 1, 2, True, True),
        ))
        with self.assertRaisesRegex(RuntimeError, "cap"):
            evidence.add(over_cap)


if __name__ == "__main__":  # pragma: no cover - direct local teaching command.
    unittest.main()
