"""Unit tests for ADTOF's scoped SIGTERM/SIGINT shutdown-event adapter.

Tests use a fake signal controller and a real in-memory threading.Event. They
never install handlers in the test process, wait for a real timeout, connect to
services, run ADTOF, loop, or create Kubernetes resources.
"""

from __future__ import annotations

import threading
import unittest

from app.runtime.shutdown_event import ADTOFShutdownWaiter, installed_adtof_shutdown_waiter


class FakeSignalController:
    """Record handler replacement/restoration without mutating process globals."""

    SIGTERM = 15
    SIGINT = 2

    def __init__(self, *, fail_signal_number: int | None = None) -> None:
        self.fail_signal_number = fail_signal_number
        self.original_term = object()
        self.original_int = object()
        self.handlers: dict[int, object] = {
            self.SIGTERM: self.original_term,
            self.SIGINT: self.original_int,
        }
        self.calls: list[tuple[str, int, object | None]] = []

    def getsignal(self, signal_number: int) -> object:
        """Return the fake current handler and record the read."""

        self.calls.append(("getsignal", signal_number, None))
        return self.handlers[signal_number]

    def signal(self, signal_number: int, handler: object) -> None:
        """Replace one fake handler, optionally failing before replacement."""

        self.calls.append(("signal", signal_number, handler))
        if signal_number == self.fail_signal_number:
            raise RuntimeError("fake install failure")
        self.handlers[signal_number] = handler


class ADTOFShutdownWaiterTests(unittest.TestCase):
    """Prove event waits are finite and shutdown observation is idempotent."""

    def test_request_shutdown_interrupts_future_wait_without_sleeping(self) -> None:
        """A signal handler can set the event before a supervisor wait begins."""

        waiter = ADTOFShutdownWaiter()
        self.assertFalse(waiter.is_shutdown_requested)
        self.assertFalse(waiter.wait_for_shutdown(0.0))
        waiter.request_shutdown()
        self.assertTrue(waiter.is_shutdown_requested)
        self.assertTrue(waiter.wait_for_shutdown(0.0))

    def test_invalid_timeout_or_event_is_rejected_before_waiting(self) -> None:
        """The adapter cannot silently become an unbounded wait primitive."""

        waiter = ADTOFShutdownWaiter()
        for timeout in (-0.1, 31.0, float("inf"), True, "1"):
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    waiter.wait_for_shutdown(timeout)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ADTOFShutdownWaiter(event=object())  # type: ignore[arg-type]


class ADTOFShutdownSignalContextTests(unittest.TestCase):
    """Prove both handlers set one event and prior process state is restored."""

    def test_both_signals_request_shutdown_and_handlers_restore_after_context(self) -> None:
        """A Pod's SIGTERM and interactive SIGINT share one safe control event."""

        controller = FakeSignalController()
        waiter = ADTOFShutdownWaiter()
        with installed_adtof_shutdown_waiter(signal_controller=controller, waiter=waiter) as active:
            self.assertIs(active, waiter)
            installed_term = controller.handlers[controller.SIGTERM]
            installed_int = controller.handlers[controller.SIGINT]
            self.assertIs(installed_term, installed_int)
            self.assertFalse(waiter.is_shutdown_requested)
            installed_term(controller.SIGTERM, None)  # type: ignore[operator]
            self.assertTrue(waiter.wait_for_shutdown(0.0))

        self.assertIs(controller.handlers[controller.SIGTERM], controller.original_term)
        self.assertIs(controller.handlers[controller.SIGINT], controller.original_int)

    def test_body_exception_and_partial_install_restore_any_replaced_handlers(self) -> None:
        """Scoped process-global state never remains half-installed after failure."""

        controller = FakeSignalController()
        with self.assertRaisesRegex(RuntimeError, "body failure"):
            with installed_adtof_shutdown_waiter(signal_controller=controller):
                raise RuntimeError("body failure")
        self.assertIs(controller.handlers[controller.SIGTERM], controller.original_term)
        self.assertIs(controller.handlers[controller.SIGINT], controller.original_int)

        partial = FakeSignalController(fail_signal_number=FakeSignalController.SIGINT)
        with self.assertRaisesRegex(RuntimeError, "fake install failure"):
            with installed_adtof_shutdown_waiter(signal_controller=partial):
                pass
        self.assertIs(partial.handlers[partial.SIGTERM], partial.original_term)
        self.assertIs(partial.handlers[partial.SIGINT], partial.original_int)

    def test_non_main_thread_rejects_before_signal_controller_mutation(self) -> None:
        """Python's process-global handler rule is enforced before fake installation."""

        controller = FakeSignalController()
        failures: list[BaseException] = []

        def attempt_install() -> None:
            try:
                with installed_adtof_shutdown_waiter(signal_controller=controller):
                    pass
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=attempt_install)
        thread.start()
        thread.join()

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)
        self.assertEqual(controller.calls, [])


if __name__ == "__main__":
    unittest.main()
