"""Optional generic dispatcher process entrypoint, kept separate from Demucs.

The existing ``python -m app.dispatcher_runtime`` entrypoint intentionally
remains Demucs-only.  This sibling entrypoint reuses its reviewed settings
parser, signal handling, bounded database-recovery policy, and supervisor loop,
but injects ``dispatch_dispatchable_once`` as the one-attempt function.

Keeping two explicit entrypoints makes a future image/Deployment rollout a
visible decision: changing a Python source file alone cannot silently broaden
the currently running dispatcher from Demucs events to every approved v004
event.  This module has no Kubernetes, broker, or database side effect merely
by being imported; its ``main`` function runs only when a later container
command deliberately selects it.
"""

from __future__ import annotations

import sys
from threading import Event

from app.amqp_publisher import (
    DispatcherPublisherConfigurationError,
    DispatcherPublisherSettings,
)
from app.dispatch_dispatchable_once import dispatch_dispatchable_once
from app.dispatcher_runtime import (
    DispatcherRuntimeConfigurationError,
    DispatcherRuntimeSettings,
    _install_shutdown_handlers,
    run_dispatcher_forever,
)
from app.postgresql import (
    DispatcherDatabaseConfigurationError,
    PsycopgDispatcherDatabase,
)


def main() -> int:
    """Run the generic dispatcher only when this explicit module is selected.

    The configuration and graceful-shutdown semantics are deliberately shared
    with the Demucs-only entrypoint.  A configuration error returns ``2`` so
    Kubernetes can expose a bad Secret/ConfigMap as a failing Pod instead of a
    process that retries forever with unsafe or incomplete settings.
    """

    try:
        runtime_settings = DispatcherRuntimeSettings.from_environment()
        publisher_settings = DispatcherPublisherSettings.from_environment()
        database = PsycopgDispatcherDatabase()
    except (
        DispatcherRuntimeConfigurationError,
        DispatcherPublisherConfigurationError,
        DispatcherDatabaseConfigurationError,
    ):
        # Keep the ordinary stable diagnostic non-sensitive.  Credentials,
        # private Service addresses, and lower-level driver errors must never
        # become normal container logs.
        print("dispatcher has invalid or incomplete configuration.", file=sys.stderr)
        return 2

    stop_event = Event()
    _install_shutdown_handlers(stop_event)
    run_dispatcher_forever(
        database=database,
        publisher_settings=publisher_settings,
        runtime_settings=runtime_settings,
        stop_requested=stop_event.is_set,
        # This is the one intentional difference from `dispatcher_runtime`.
        # The shared supervisor never chooses a stage family by configuration;
        # this explicit container command selects the reviewed generic path.
        dispatch=dispatch_dispatchable_once,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
