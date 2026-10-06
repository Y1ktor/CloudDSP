"""Launch one reviewed Demucs CPU command with the local ARM64 workaround.

Docker Desktop's Linux/ARM64 virtual CPU can advertise an instruction surface
which lets Torch's oneDNN/MKLDNN backend choose a Demucs model kernel that
ends with ``SIGILL``.  That was reproduced inside the deployed k3d worker: the
same fixed CPU command failed through the ordinary ``demucs`` console script
but wrote both expected stems when MKLDNN was disabled before Demucs imported.

This module is deliberately a tiny process-local launcher, not a worker
controller.  It does not know a task ID, touch PostgreSQL, MinIO, RabbitMQ, or
Kubernetes, and it accepts no configuration from environment variables.  The
parent command builder still owns the allowed model, CPU device, source path,
and output path.  A future Linux/NVIDIA GPU image must not reuse this launcher:
that profile should keep its separately validated Torch backend settings.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


class DemucsCpuCliConfigurationError(RuntimeError):
    """The fixed local CPU image lacks the Torch backend this launcher needs."""


def _load_torch() -> Any:
    """Import Torch only when the child process actually starts.

    Importing here rather than at module load preserves the critical ordering:
    the launcher changes the backend flag before the later Demucs import can
    construct model code.  It also keeps source-only unit tests independent of
    a local Torch installation.
    """

    import torch

    return torch


def _load_demucs_main() -> Callable[[Sequence[str] | None], object]:
    """Load Demucs only after the local Torch CPU policy has been applied."""

    from demucs.separate import main

    return main


def disable_mkldnn_for_local_arm64_cpu(torch_module: object) -> None:
    """Disable the reproduced SIGILL path and prove the setting took effect.

    ``torch.backends.mkldnn.enabled`` is an in-process process-wide switch.
    The actual worker starts each separation in a separate child process, so
    this change cannot leak from one leased task into the PID-1 supervisor or
    another worker Pod.  Treat an absent, malformed, or immutable backend as
    an image configuration failure rather than silently invoking the unsafe
    default execution path.
    """

    try:
        mkldnn_backend = torch_module.backends.mkldnn  # type: ignore[attr-defined]
        mkldnn_backend.enabled = False
        is_disabled = mkldnn_backend.enabled is False
    except (AttributeError, TypeError) as error:
        raise DemucsCpuCliConfigurationError(
            "The local Demucs CPU backend is unavailable."
        ) from error
    if not is_disabled:
        raise DemucsCpuCliConfigurationError("The local Demucs CPU backend could not be disabled.")


def run_demucs_cpu_cli(
    arguments: Sequence[str] | None = None,
    *,
    torch_loader: Callable[[], object] = _load_torch,
    demucs_main_loader: Callable[[], Callable[[Sequence[str] | None], object]] = _load_demucs_main,
) -> None:
    """Apply the fixed local policy, then delegate the reviewed argv to Demucs.

    Passing ``None`` preserves the normal command-line behavior: Demucs reads
    the process's already reviewed ``sys.argv``.  Tests pass a sequence only to
    verify forwarding without importing Torch or loading a model.  This module
    intentionally does not catch the model's exit/error behavior; the parent
    subprocess adapter owns the safe process-result category and lease-aware
    cleanup.
    """

    disable_mkldnn_for_local_arm64_cpu(torch_loader())
    demucs_main = demucs_main_loader()
    # Demucs accepts ``None`` as its signal to parse the child's native argv.
    # A concrete list makes injected tests and any future explicit caller use
    # the same parser surface without exposing a shell or extra options.
    demucs_main(None if arguments is None else list(arguments))


if __name__ == "__main__":
    run_demucs_cpu_cli()
