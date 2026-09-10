# This pinned Docker Official Python OCI index selects its Linux/ARM64 child on
# the current Apple-Silicon k3d cluster. The identical index can later select
# Linux/AMD64 for the separate native GPU profile; the dispatcher itself does
# not need CUDA or direct access to a GPU.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# A future Pod will use a read-only root filesystem. Prevent Python from trying
# to create bytecode there, and flush the intentionally small operational logs
# promptly so `kubectl logs` shows bounded supervisor recovery messages.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the hash-locked dependencies before application source. Docker can reuse
# this verified wheel layer for a source-only change, while `--require-hashes`
# fails the build if an upstream package differs from reviewed bytes.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --require-hashes \
    --requirement requirements.lock

# Tests exist only in this temporary validation stage. They use fakes, so the
# build validates lease ordering, publisher-confirmation behavior, database
# recovery, and signal handling without a PostgreSQL, RabbitMQ, Secret, or Pod.
COPY app ./app
COPY tests ./tests
RUN PYTHONPATH=/app python -m unittest discover --start-directory tests --verbose

# The final stage starts from exactly the same immutable Python base. Copying
# validated site packages avoids a second dependency download/install and keeps
# test source out of the long-running internal dispatcher image.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Both stages are Python 3.12 from the same digest, so this is the exact global
# package directory used by the final interpreter. It contains only Pika,
# Psycopg, and locked transitive packages—not test files or build tooling.
COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images begin as root. The dispatcher only makes
# outgoing connections to private PostgreSQL/RabbitMQ Services, so it needs no
# privileged port, writable system path, Docker socket, or Kubernetes API
# credential. A named build-time account documents the numeric runtime identity
# that a later Deployment will reinforce with a Pod securityContext.
RUN groupadd --gid 10001 clouddspdispatcher \
    && useradd --uid 10001 --gid clouddspdispatcher --create-home \
      --shell /usr/sbin/nologin clouddspdispatcher

# The runtime receives only the tested dispatcher package. Kubernetes injects
# the limited database and publisher Secrets when a future Pod starts; no
# endpoint, password, durable event, or environment-specific value is baked in.
COPY --from=validation --chown=10001:10001 /app/app ./app

# Compile source without writing `.pyc` files. A syntax issue therefore stops
# the image build before the local registry can receive invalid runtime bytes.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path("app").glob("*.py")]'

USER 10001:10001

# This dispatcher is outbound-only: it leases PostgreSQL outbox rows and
# publishes to RabbitMQ. It exposes no HTTP port, so a future Deployment needs
# neither a Kubernetes Service nor browser-facing Ingress for this component.
ENTRYPOINT ["python", "-m", "app.dispatcher_runtime"]
