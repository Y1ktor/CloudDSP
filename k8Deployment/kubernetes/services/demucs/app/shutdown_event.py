"""Provide one main-thread SIGTERM/SIGINT event for Demucs supervisor waits.

Kubernetes normally terminates a Pod with ``SIGTERM`` and may later use
``SIGKILL`` if the process outlives its grace deadline. A worker must therefore
interrupt an idle/backoff wait before another RabbitMQ receive or recovery
scan. This bridge maps both supported signals to one ``threading.Event`` whose
wait method satisfies the existing supervisor shutdown-waiter protocol.

Signal handlers are process-global Python state. Installation is main-thread
only and scoped by a context manager that restores prior handlers on normal
exit, an exception, or partial installation failure. The async handler only
sets the Event: it performs no logging, service call, lock, database work, or
model cleanup.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import math
import signal
import threading
from types import FrameType
from typing import Any, Protocol


# The supervisor policy caps normal idle/backoff delays at 30 seconds. This
# duplicate finite bound prevents a direct caller from turning the bridge into
# an unbounded blocking primitive outside the reviewed control flow.
MAX_DEMUCS_SHUTDOWN_WAIT_SECONDS = 30.0


class DemucsShutdownSignalController(Protocol):
    """The tiny process-global signal surface used by this context manager.

    Tests provide a fake controller to avoid changing real SIGTERM/SIGINT
    handlers. Production uses Python's standard ``signal`` module; this is not
    a Kubernetes API, shell command, or container lifecycle interface.
    """

    SIGTERM: int
    SIGINT: int

    def getsignal(self, signal_number: int) -> Any:
        """Return the currently installed handler for a supported signal."""

    def signal(self, signal_number: int, handler: Any) -> Any:
        """Install a handler and return its replaced handler if supported."""


class DemucsShutdownWaiter:
    """Event-backed concrete implementation of the supervisor wait protocol.

    A future entrypoint can also call ``request_shutdown`` during a controlled
    shutdown. This object does not itself install signals, own a worker loop,
    manage clients, or make a Kubernetes lifecycle decision.
    """

    def __init__(self, event: threading.Event | None = None) -> None:
        """Use one supplied Event or a private initially-clear Event."""

        if event is not None and not isinstance(event, threading.Event):
            raise TypeError("event must be threading.Event.")
        self._event = event if event is not None else threading.Event()

    @property
    def is_shutdown_requested(self) -> bool:
        """Return a non-blocking event observation for a future loop boundary."""

        return self._event.is_set()

    def request_shutdown(self) -> None:
        """Idempotently set shutdown without I/O or process-exit policy."""

        self._event.set()

    def wait_for_shutdown(self, timeout_seconds: float) -> bool:
        """Wait once with the same finite bound as supervisor policy delays."""

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("Demucs shutdown wait timeout must be numeric.")
        if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= MAX_DEMUCS_SHUTDOWN_WAIT_SECONDS:
            raise ValueError("Demucs shutdown wait timeout is outside its bounded range.")
        shutdown_requested = self._event.wait(timeout_seconds)
        if type(shutdown_requested) is not bool:  # Defensive guard for forged event doubles.
            raise TypeError("Demucs shutdown event returned an invalid result.")
        return shutdown_requested


def _require_main_thread() -> None:
    """Reject handler installation before any unsafe process-global mutation."""

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Demucs shutdown handlers must be installed on the main thread.")


@contextmanager
def installed_demucs_shutdown_waiter(
    *,
    signal_controller: DemucsShutdownSignalController = signal,
    waiter: DemucsShutdownWaiter | None = None,
) -> Iterator[DemucsShutdownWaiter]:
    """Install SIGTERM/SIGINT Event handlers and restore prior state on exit.

    The optional waiter lets a future entrypoint share an Event across an outer
    loop and scoped handler installation. If installing the second handler
    fails, any first replacement is restored before the original error escapes.
    Cleanup attempts reverse-order restoration and exposes its first error only
    when the context body succeeded.
    """

    _require_main_thread()
    if waiter is not None and not isinstance(waiter, DemucsShutdownWaiter):
        raise TypeError("waiter must be DemucsShutdownWaiter.")
    # This Protocol is structural, enabling a test fake. Validate its signal
    # constants and methods explicitly rather than using isinstance(Protocol).
    if (
        not isinstance(getattr(signal_controller, "SIGTERM", None), int)
        or not isinstance(getattr(signal_controller, "SIGINT", None), int)
        or not callable(getattr(signal_controller, "getsignal", None))
        or not callable(getattr(signal_controller, "signal", None))
    ):
        raise TypeError("signal_controller is invalid.")

    active_waiter = waiter if waiter is not None else DemucsShutdownWaiter()
    installed: list[tuple[int, Any]] = []

    def request_shutdown(_signal_number: int, _frame: FrameType | None) -> None:
        """Restrict asynchronous signal work to one in-memory Event.set call."""

        active_waiter.request_shutdown()

    try:
        for signal_number in (signal_controller.SIGTERM, signal_controller.SIGINT):
            previous_handler = signal_controller.getsignal(signal_number)
            signal_controller.signal(signal_number, request_shutdown)
            installed.append((signal_number, previous_handler))
    except BaseException:
        # A partial install must never leave only one original handler
        # replaced. Preserve the installation error; restore best-effort.
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
