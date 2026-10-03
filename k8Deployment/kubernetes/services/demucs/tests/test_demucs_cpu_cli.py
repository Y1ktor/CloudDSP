"""Unit tests for the local ARM64 CPU Demucs child launcher.

These tests use small in-memory fakes.  They do not import Torch or Demucs,
load model weights, start a child process, access a Kubernetes resource, or
contact PostgreSQL, MinIO, or RabbitMQ.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.processing.demucs_cpu_cli import (
    DemucsCpuCliConfigurationError,
    disable_mkldnn_for_local_arm64_cpu,
    run_demucs_cpu_cli,
)


class DemucsCpuCliTests(unittest.TestCase):
    """Keep backend selection ordered, checked, and independent of task data."""

    def test_launcher_disables_backend_before_loading_demucs_and_forwards_argv(self) -> None:
        """The model loader observes the safety setting before any import work."""

        torch_module = SimpleNamespace(
            backends=SimpleNamespace(mkldnn=SimpleNamespace(enabled=True))
        )
        observed: dict[str, object] = {}

        def demucs_main_loader():
            observed["enabled_when_demucs_loaded"] = torch_module.backends.mkldnn.enabled

            def demucs_main(arguments):
                observed["arguments"] = arguments

            return demucs_main

        run_demucs_cpu_cli(
            ("--device", "cpu", "source.media"),
            torch_loader=lambda: torch_module,
            demucs_main_loader=demucs_main_loader,
        )

        self.assertIs(torch_module.backends.mkldnn.enabled, False)
        self.assertIs(observed["enabled_when_demucs_loaded"], False)
        self.assertEqual(observed["arguments"], ["--device", "cpu", "source.media"])

    def test_launcher_preserves_native_argv_when_no_explicit_arguments_are_given(self) -> None:
        """The subprocess form relies on Demucs parsing its reviewed OS argv."""

        torch_module = SimpleNamespace(
            backends=SimpleNamespace(mkldnn=SimpleNamespace(enabled=True))
        )
        observed: list[object] = []

        run_demucs_cpu_cli(
            torch_loader=lambda: torch_module,
            demucs_main_loader=lambda: lambda arguments: observed.append(arguments),
        )

        self.assertEqual(observed, [None])

    def test_missing_or_malformed_backend_fails_before_demucs_loader_runs(self) -> None:
        """The launcher must never fall back to the unsafe default model path."""

        demucs_loader_calls = 0

        def demucs_main_loader():
            nonlocal demucs_loader_calls
            demucs_loader_calls += 1
            return lambda arguments: None

        with self.assertRaises(DemucsCpuCliConfigurationError):
            run_demucs_cpu_cli(
                torch_loader=lambda: object(),
                demucs_main_loader=demucs_main_loader,
            )

        self.assertEqual(demucs_loader_calls, 0)

    def test_read_back_guard_rejects_a_backend_that_ignores_disable_request(self) -> None:
        """An immutable/unsupported Torch backend cannot silently continue."""

        class IgnoringMkldnnBackend:
            """Mimic a backend that accepts assignment but remains enabled."""

            @property
            def enabled(self) -> bool:
                """Expose the unsafe value regardless of attempted assignment."""

                return True

            @enabled.setter
            def enabled(self, value: object) -> None:
                """Discard the attempted change to exercise the read-back check."""

        torch_module = SimpleNamespace(
            backends=SimpleNamespace(mkldnn=IgnoringMkldnnBackend())
        )

        with self.assertRaises(DemucsCpuCliConfigurationError):
            disable_mkldnn_for_local_arm64_cpu(torch_module)


if __name__ == "__main__":
    unittest.main()
