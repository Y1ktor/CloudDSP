"""Provide one main-thread SIGTERM/SIGINT event for ADTOF supervisor waits.

Kubernetes normally terminates a Pod by sending ``SIGTERM`` and later may send
``SIGKILL`` if the process has not exited by its grace deadline. A worker must
therefore be able to interrupt its bounded idle/backoff wait before it begins a
new RabbitMQ receive or recovery scan. This module owns that narrow bridge:
both supported signals set one ``threading.Event`` and the event implements the
existing supervisor shutdown-waiter protocol.

Signal handlers are process-global Python state, so installation is restricted
to the main thread and is scoped to a context manager. The prior handlers are
restored on normal exit, an exception, or a partial installation failure. The
handler does only ``Event.set()``, avoiding logging, service calls, locks,
database work, or model cleanup inside Python's asynchronous signal callback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import math
import signal
import threading
from types import FrameType
from typing import Any, Protocol


# Supervisor decisions already cap idle/backoff waits at 30 seconds. Repeating
# that finite cap here prevents a direct caller from turning this tiny adapter
# into an unbounded blocking primitive outside the reviewed supervisor policy.
MAX_ADTOF_SHUTDOWN_WAIT_SECONDS = 30.0


class ADTOFShutdownSignalController(Protocol):
    """The small process-global signal surface used by the context manager.

    A test fake can implement this protocol without touching the test process's
    real SIGTERM/SIGINT handlers. Production passes Python's standard
    ``signal`` module, not Kubernetes or a shell command.
    """

    SIGTERM: int
    SIGINT: int

    def getsignal(self, signal_number: int) -> Any:
        """Return the currently installed handler for one supported signal."""

    def signal(self, signal_number: int, handler: Any) -> Any:
        """Install one handler and return the previous handler if supported."""


class ADTOFShutdownWaiter:
    """One event-backed implementation of the supervisor wait protocol.

    The event can also be set programmatically by a future entrypoint during
    controlled shutdown. It contains no worker-loop, client, signal-install,
    or Kubernetes lifecycle behavior by itself.
    """

    def __init__(self, event: threading.Event | None = None) -> None:
        """Use one supplied event or allocate one private, initially-clear event."""

        if event is not None and not isinstance(event, threading.Event):
            raise TypeError("event must be threading.Event.")
        self._event = event if event is not None else threading.Event()

    @property
    def is_shutdown_requested(self) -> bool:
        """Expose a non-blocking shutdown observation for a future loop boundary."""

        return self._event.is_set()

    def request_shutdown(self) -> None:
        """Set the event without performing I/O or choosing process exit behavior."""

        self._event.set()

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait once for shutdown with the same finite bound as supervisor policy."""

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("ADTOF shutdown wait timeout must be numeric.")
        if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= MAX_ADTOF_SHUTDOWN_WAIT_SECONDS:
            raise ValueError("ADTOF shutdown wait timeout is outside its bounded range.")
        shutdown_requested = self._event.wait(timeout_seconds)
        if type(shutdown_requested) is not bool:  # Defensive guard for a forged event double.
            raise TypeError("ADTOF shutdown event returned an invalid result.")
        return shutdown_requested


def _require_main_thread() -> None:
    """Reject unsafe handler installation before any process-global mutation."""

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("ADTOF shutdown handlers must be installed on the main thread.")


@contextmanager
def installed_adtof_shutdown_waiter(
    *,
    signal_controller: ADTOFShutdownSignalController = signal,
    waiter: ADTOFShutdownWaiter | None = None,
) -> Iterator[ADTOFShutdownWaiter]:
    """Install SIGTERM/SIGINT event handlers and restore prior handlers on exit.

    The optional waiter lets an entrypoint share the same event across an outer
    loop and this scoped signal installation. If installing the second handler
    fails, already-replaced handlers are restored before the original error
    propagates. Cleanup attempts every restoration in reverse order and
    preserves the first cleanup failure only when the body itself succeeded.
    """

    _require_main_thread()
    if waiter is not None and not isinstance(waiter, ADTOFShutdownWaiter):
        raise TypeError("waiter must be ADTOFShutdownWaiter.")
    # The signal-controller protocol is intentionally structural so fake tests
    # can avoid process-global handler mutation. Validate its two constants and
    # two methods explicitly instead of using ``isinstance`` on a Protocol.
    if (
        not isinstance(getattr(signal_controller, "SIGTERM", None), int)
        or not isinstance(getattr(signal_controller, "SIGINT", None), int)
        or not callable(getattr(signal_controller, "getsignal", None))
        or not callable(getattr(signal_controller, "signal", None))
    ):
        raise TypeError("signal_controller is invalid.")

    active_waiter = waiter if waiter is not None else ADTOFShutdownWaiter()
    installed: list[tuple[int, Any]] = []

    def request_shutdown(_signal_number: int, _frame: FrameType | None) -> None:
        """Keep async signal work limited to an idempotent in-memory event set."""

        active_waiter.request_shutdown()

    try:
        for signal_number in (signal_controller.SIGTERM, signal_controller.SIGINT):
            previous_handler = signal_controller.getsignal(signal_number)
            signal_controller.signal(signal_number, request_shutdown)
            installed.append((signal_number, previous_handler))
    except BaseException:
        # A partial install must not leave a test process or worker with only
        # one of its original handlers replaced. Preserve the install error;
        # restoration is best-effort because a failed signal subsystem cannot
        # safely be repaired here.
        for signal_number, previous_handler in reversed(installed):
            try:
                signal_controller.signal(signal_number, previous_handler)
            except BaseException:
                pass
        raise

    body_error: BaseException | None = None
    try:
        yield active_waiter
    except BaseException as error:
        body_error = error
        raise
    finally:
        restore_error: BaseException | None = None
        for signal_number, previous_handler in reversed(installed):
            try:
                signal_controller.signal(signal_number, previous_handler)
            except BaseException as error:
                if restore_error is None:
                    restore_error = error
        if body_error is None and restore_error is not None:
            raise restore_error
