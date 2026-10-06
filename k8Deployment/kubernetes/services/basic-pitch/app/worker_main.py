"""Keep the stable container command separate from worker runtime policy.

Helm and the image invoke ``python -m app.worker_main``. The runtime package
owns signal handling, safe configuration diagnostics, and dependency cleanup;
this launcher only translates its explicit return status to the process exit.
"""

from app.runtime.worker_main import main


if __name__ == "__main__":
    raise SystemExit(main())
