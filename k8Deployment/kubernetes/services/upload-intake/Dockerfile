# Use the existing Kubernetes-local Python runtime lock rather than a mutable
# `python:3.12-slim` tag. Docker selects its Linux/ARM64 child image for the
# current Apple-Silicon k3d profile; the same OCI index can select amd64 for a
# later native Linux profile.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# A future Pod may use a read-only root filesystem. Avoid writing `.pyc` files
# there, and emit the process's deliberately minimal operational messages
# immediately through `kubectl logs` rather than buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the dependency lock separately so a source-only change can reuse the
# expensive, verified wheel-install layer. `--require-hashes` fails closed if
# an upstream artifact is not the exact reviewed bytes in requirements.lock.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --require-hashes \
    --requirement requirements.lock

# Tests remain only in this validation stage. They use fakes, so the build
# validates parser, transaction, MinIO, RabbitMQ, shutdown, and reconnect
# behavior without requiring a cluster, credentials, or network service.
COPY app ./app
COPY tests ./tests
RUN PYTHONPATH=/app python -m unittest discover --start-directory tests --verbose

# Start a separate final stage from the identical immutable Python image.
# Copying the validated site-packages avoids a second package installation and
# prevents test files from becoming part of the long-running runtime image.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Both stages use Python 3.12 from the same pinned base. This is therefore the
# exact global site-packages directory the final interpreter imports. It holds
# only the hashed application dependencies and their reviewed transitive wheels.
COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images begin as root. This worker never needs to bind
# a port, write application files, use Docker, or call the Kubernetes API, so
# it runs as a dedicated numeric non-root account before Pod hardening exists.
RUN groupadd --gid 10001 clouddspintake \
    && useradd --uid 10001 --gid clouddspintake --create-home \
      --shell /usr/sbin/nologin clouddspintake

# Runtime source is copied only after tests pass. It contains no test code,
# credentials, Secret values, image-build tools, Kubernetes manifest, listener,
# or browser-facing HTTP endpoint.
COPY --from=validation --chown=10001:10001 /app/app ./app

# Compile the exact runtime source tree without generating root-owned `.pyc`
# files. This makes a syntax error fail the image build before registry push.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

USER 10001:10001

# The worker makes outgoing connections only to private ClusterIP Services. It
# exposes no port: a future Deployment needs neither Service nor Ingress for
# this queue consumer. Kubernetes injects each least-privilege Secret at Pod
# start; the image itself contains neither credentials nor cluster addresses.
ENTRYPOINT ["python", "-m", "app.consumer_runtime"]
