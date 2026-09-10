# This image is a disposable MinIO-to-RabbitMQ transport-test client. It is
# not the future upload-intake service, a RabbitMQ broker, or a place to fetch
# packages when a Kubernetes Job starts. Its pinned base is recorded in
# `../../../images.lock.yaml` as `rabbitmq-amqp-client-python`.
FROM docker.io/library/python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

# Avoid bytecode on a later read-only root filesystem and flush short-lived
# Job logs immediately so `kubectl logs` shows a clear success/failure result.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install exactly the reviewed Pika wheel. `--no-deps` is safe because this
# pure-Python package has no runtime dependency, and `--require-hashes` blocks
# an unexpected package artifact.
COPY requirements.txt ./requirements.txt
RUN python -m pip install \
    --root-user-action=ignore \
    --no-cache-dir \
    --no-deps \
    --require-hashes \
    --requirement requirements.txt

# The Python Official Image begins as root. Create the explicit non-root
# identity that the later smoke Job uses instead of relying only on a Pod-level
# numeric override.
RUN groupadd --gid 10001 sourceintakesmoke \
    && useradd --uid 10001 --gid 10001 --create-home \
      --shell /usr/sbin/nologin sourceintakesmoke

# Keep the pure event matcher and AMQP boundary in the final image. The unit
# tests live under `kubernetes/tests/` and run on the host before this build;
# only runtime source belongs in this disposable client image.
COPY --chown=10001:10001 minio_source_intake_event.py ./minio_source_intake_event.py
COPY --chown=10001:10001 minio_source_intake_smoke_client.py ./minio_source_intake_smoke_client.py

# Compile without `compileall` so the build validates both modules but leaves
# no root-owned .pyc files in the image filesystem.
RUN python -c 'from pathlib import Path; [compile(path.read_text(encoding="utf-8"), str(path), "exec") for path in Path(".").glob("*.py")]'

USER 10001:10001

# Kubernetes supplies AMQP endpoint, credentials, queue, test object key, and
# mode through Secret/configured environment variables. The image contains no
# cluster address, S3 credential, test object, or hard-coded queue operation.
ENTRYPOINT ["python", "/app/minio_source_intake_smoke_client.py"]
