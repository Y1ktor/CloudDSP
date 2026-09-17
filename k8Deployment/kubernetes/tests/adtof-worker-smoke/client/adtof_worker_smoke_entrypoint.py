"""Run the bounded fixed-coordinate ADTOF worker smoke facade as a Job process.

This module is the thin runtime control layer above the already-reviewed
settings, factories, fixed-key adapters, state machine, and composition facade.
It owns environment loading, monotonic elapsed time, a small polling cadence,
safe milestone output, and process exit status. It contains no SQL, object-key
construction, RabbitMQ call, model invocation, client-policy decision, image,
or Kubernetes manifest.

The eventual Kubernetes Job will run :func:`main` once. A post-prepare timeout,
worker failure, or output-verification failure exits non-zero and intentionally
preserves fixed evidence for diagnosis. The successful path performs the
state-machine-approved MinIO object cleanup before guarded PostgreSQL cleanup;
no retry loop is hidden in this entrypoint.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Mapping

from adtof_worker_smoke_composition import (
    ADTOFWorkerSmokeCompositionError,
    ADTOFWorkerSmokeCompositionFacade,
)
from adtof_worker_smoke_contract import ADTOFWorkerSmokeConfigurationError, ADTOFWorkerSmokeSettings
from adtof_worker_smoke_orchestration import ADTOFWorkerSmokeAction, ADTOFWorkerSmokeOutcome
from adtof_worker_smoke_runtime import build_adtof_worker_smoke_composition


# This is a runtime cadence, not a durable retry/backoff policy. PostgreSQL and
# RabbitMQ continue to own worker retries. Two seconds avoids a busy loop while
# still making a local smoke test responsive; state-machine timeout remains the
# absolute upper bound on this wait.
ADTOF_WORKER_SMOKE_OBSERVATION_POLL_SECONDS = 2.0


class ADTOFWorkerSmokeEntrypointError(RuntimeError):
    """A clock/facade invariant failed before a safe terminal outcome was available."""


_MILESTONE_BY_ACTION = {
    ADTOFWorkerSmokeAction.ASSERT_FIXED_OBJECTS_ABSENT: "checking fixed object preflight",
    ADTOFWorkerSmokeAction.UPLOAD_CONTROLLED_WAV: "uploading controlled drums WAV",
    ADTOFWorkerSmokeAction.PREPARE_FIXED_EVENT: "creating durable Job and outbox event",
    ADTOFWorkerSmokeAction.READ_VERIFY_OUTPUTS: "verifying worker-produced MIDI and tempo outputs",
    ADTOFWorkerSmokeAction.DELETE_FIXED_OBJECTS: "cleaning verified fixed MinIO objects",
    ADTOFWorkerSmokeAction.CLEANUP_FIXED_DATABASE_EVENT: "cleaning guarded PostgreSQL evidence",
}

_TERMINAL_MESSAGE_BY_OUTCOME = {
    ADTOFWorkerSmokeOutcome.PASSED: "passed",
    ADTOFWorkerSmokeOutcome.TIMED_OUT_PRESERVING_EVIDENCE: "timed out; preserving evidence",
    ADTOFWorkerSmokeOutcome.FAILED_PRESERVING_EVIDENCE: "failed; preserving evidence",
    ADTOFWorkerSmokeOutcome.OBJECT_CLEANUP_INCOMPLETE: "object cleanup incomplete",
    ADTOFWorkerSmokeOutcome.DATABASE_CLEANUP_INCOMPLETE: "database cleanup incomplete",
}


def _elapsed_seconds(*, started_at: float, now: float) -> int:
    """Convert a monotonic timestamp pair to a non-negative whole-second observation age."""

    if (
        not isinstance(started_at, (int, float))
        or not isinstance(now, (int, float))
        or not math.isfinite(started_at)
        or not math.isfinite(now)
        or now < started_at
    ):
        raise ADTOFWorkerSmokeEntrypointError("ADTOF smoke monotonic clock is invalid.")
    return math.floor(now - started_at)


def _report(report: Callable[[str], None], message: str) -> None:
    """Emit one non-sensitive, stable milestone string for `kubectl logs`."""

    report(f"ADTOF worker smoke: {message}")


def _terminal_outcome_or_raise(
    facade: ADTOFWorkerSmokeCompositionFacade,
) -> ADTOFWorkerSmokeOutcome:
    """Require a facade failure to have been classified by its state machine."""

    outcome = facade.state.outcome
    if outcome is None:
        raise ADTOFWorkerSmokeEntrypointError("ADTOF smoke action failed without a terminal state.")
    return outcome


def run_adtof_worker_smoke(
    facade: ADTOFWorkerSmokeCompositionFacade,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    report: Callable[[str], None] = print,
) -> ADTOFWorkerSmokeOutcome:
    """Drive one already-wired facade until a truthful terminal state is reached.

    The facade performs only its currently permitted action. This loop calls
    `sleep` only while a completed `observe` action still requests observation;
    no upload/database/output/cleanup error is retried. Callers inject the
    monotonic clock and sleeper in tests, keeping unit tests instantaneous and
    preventing any dependence on wall-clock jumps.
    """

    # Keep this boundary structural so its runtime control can be unit-tested
    # with an in-memory facade. The production builder still returns the real
    # `ADTOFWorkerSmokeCompositionFacade`; no alternate external adapter is
    # accepted or constructed here.
    if not callable(getattr(facade, "advance_one", None)) or not hasattr(facade, "state"):
        raise TypeError("facade must expose state and advance_one().")
    observation_started_at: float | None = None
    announced_observation = False

    while facade.state.outcome is None:
        action = facade.state.next_action
        if action is None:
            raise ADTOFWorkerSmokeEntrypointError("ADTOF smoke state has no next action.")

        if action is ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT:
            if observation_started_at is None:
                observation_started_at = monotonic()
            if not announced_observation:
                _report(report, "waiting for deployed dispatcher and ADTOF worker")
                announced_observation = True
            elapsed = _elapsed_seconds(started_at=observation_started_at, now=monotonic())
            try:
                facade.advance_one(elapsed_observation_seconds=elapsed)
            except ADTOFWorkerSmokeCompositionError:
                outcome = _terminal_outcome_or_raise(facade)
                _report(report, _TERMINAL_MESSAGE_BY_OUTCOME[outcome])
                return outcome

            # The facade/state machine determines timeout and terminal worker
            # facts before this point. Sleep only when it explicitly remains in
            # observation, and never overshoot the finite remaining timeout.
            if facade.state.next_action is ADTOFWorkerSmokeAction.OBSERVE_FIXED_EVENT:
                remaining = facade.state.observation_timeout_seconds - elapsed
                if remaining > 0:
                    sleep(min(ADTOF_WORKER_SMOKE_OBSERVATION_POLL_SECONDS, float(remaining)))
            continue

        _report(report, _MILESTONE_BY_ACTION[action])
        try:
            facade.advance_one()
        except ADTOFWorkerSmokeCompositionError:
            outcome = _terminal_outcome_or_raise(facade)
            _report(report, _TERMINAL_MESSAGE_BY_OUTCOME[outcome])
            return outcome

    outcome = facade.state.outcome
    if outcome is None:
        raise ADTOFWorkerSmokeEntrypointError("ADTOF smoke terminal state is invalid.")
    _report(report, _TERMINAL_MESSAGE_BY_OUTCOME[outcome])
    return outcome


def run_adtof_worker_smoke_entrypoint(
    environment: Mapping[str, str],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    report: Callable[[str], None] = print,
) -> int:
    """Load fixed settings, run once, and return a shell-compatible exit status.

    Setup failures receive one safe category. Workflow failures have already
    been classified and reported by :func:`run_adtof_worker_smoke`; they return
    `1` without printing raw connection/SDK/credential details a second time.
    """

    try:
        settings = ADTOFWorkerSmokeSettings.from_environment(environment)
        facade = build_adtof_worker_smoke_composition(
            settings,
            observation_timeout_seconds=settings.observation_timeout_seconds,
        )
        outcome = run_adtof_worker_smoke(
            facade,
            monotonic=monotonic,
            sleep=sleep,
            report=report,
        )
        return 0 if outcome is ADTOFWorkerSmokeOutcome.PASSED else 1
    except ADTOFWorkerSmokeConfigurationError:
        _report(report, "runtime configuration is invalid")
        return 1
    except Exception:
        # Factories already redact dependency/SDK/database setup details. This
        # final boundary does the same for an unexpected startup regression.
        _report(report, "runtime setup is unavailable")
        return 1


def main() -> int:
    """Run once using the future Kubernetes Job environment and standard streams."""

    return run_adtof_worker_smoke_entrypoint(
        os.environ,
        report=lambda message: print(message, flush=True),
    )


if __name__ == "__main__":
    raise SystemExit(main())
