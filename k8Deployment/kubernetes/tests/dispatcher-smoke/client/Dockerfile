# This disposable test image runs the normal-upload orchestrator and its
# restricted dispatcher verifier together in one future Kubernetes Job Pod.
# It is not a long-running worker, web API, RabbitMQ broker, or a place to
# install packages after a Pod starts. The pinned official Python base is
# recorded as `images.api-python-runtime` in ../../../images.lock.yaml.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS validation

# The future Job uses a read-only root filesystem. Avoid Python bytecode files
# there, and flush the intentionally short test progress logs for `kubectl
# logs` without exposing credentials, tokens, form fields, or object keys.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Hash verification ensures the image cannot silently install a changed PyPI
# artifact. Boto3 exists only for scoped MinIO cleanup; Psycopg reads/deletes
# the test-owned row; Pika reads exactly one controlled Demucs delivery.
COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --require-hashes \
    --requirement requirements.lock

# Keep unit tests in the temporary validation stage. They use fakes and prove
# route validation, job/event scoping, bounded polling, and safe queue handling
# without a Keycloak server, Job API, MinIO, PostgreSQL, RabbitMQ, or cluster.
COPY dispatcher_smoke_client.py ./dispatcher_smoke_client.py
COPY dispatcher_smoke_orchestrator.py ./dispatcher_smoke_orchestrator.py
COPY test_dispatcher_smoke_client.py ./test_dispatcher_smoke_client.py
COPY test_dispatcher_smoke_orchestrator.py ./test_dispatcher_smoke_orchestrator.py
RUN python -m unittest discover --pattern 'test_*.py' --verbose

# The runtime is built from the exact same Python digest. Copying the already
# hash-verified packages avoids a second package download and keeps test source
# out of the future credential-bearing Pod.
FROM docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=validation /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Docker Official Python images begin as root. This explicit account matches
# the numeric Kubernetes security context planned for the smoke Job. It needs
# no privileged port, Docker socket, shell, Kubernetes API token, or writable
# filesystem—only private Service connections supplied at run time.
RUN groupadd --gid 10001 clouddspsmoke \
    && useradd --uid 10001 --gid clouddspsmoke --create-home \
      --shell /usr/sbin/nologin clouddspsmoke

# The final image contains only the two runtime modules. Environment values and
# Secrets are intentionally absent; a later Job supplies private endpoints and
# temporary test credentials through Kubernetes at execution time.
COPY --from=validation --chown=10001:10001 /app/dispatcher_smoke_client.py ./dispatcher_smoke_client.py
COPY --from=validation --chown=10001:10001 /app/dispatcher_smoke_orchestrator.py ./dispatcher_smoke_orchestrator.py

# Compile source without retaining root-owned `.pyc` files. A syntax error thus
# stops the local build before invalid bytes can be pushed into the registry.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10001:10001

# The single entrypoint performs the bounded sequence once, cleans up its own
# records, and exits. It exposes no HTTP port, so the future Job needs neither
# a Service nor an Ingress.
ENTRYPOINT ["python", "/app/dispatcher_smoke_orchestrator.py"]
