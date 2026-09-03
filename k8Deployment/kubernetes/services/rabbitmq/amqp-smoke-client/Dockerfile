# This image is a tiny, purpose-built AMQP smoke client.  It is not a RabbitMQ
# broker, a CloudDSP processing worker, or a place to install dependencies at
# Job startup.  The pinned Python base and Pika wheel hash are documented in
# `../../../images.lock.yaml`.
FROM docker.io/library/python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

# Do not create Python bytecode in the image or a later read-only Job filesystem.
# Unbuffered output makes the short-lived Job's success/failure log immediate.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install exactly the reviewed wheel.  `--require-hashes` rejects any artifact
# whose content differs from requirements.txt, while `--no-deps` documents that
# Pika 1.4.4 is the smoke client's only runtime Python dependency.
COPY requirements.txt ./requirements.txt
RUN python -m pip install --root-user-action=ignore --no-cache-dir --no-deps --require-hashes -r requirements.txt

# The Python Official Image begins as root.  Create a dedicated unprivileged
# runtime account instead of relying on a numeric Kubernetes override alone.
RUN groupadd --gid 10001 amqpclient \
    && useradd --uid 10001 --gid amqpclient --create-home --shell /usr/sbin/nologin amqpclient

COPY --chown=10001:10001 amqp_smoke_client.py ./amqp_smoke_client.py
# Catch a syntax error while building without emitting a root-owned .pyc file
# into the final image's application directory.
RUN python -c 'compile(open("amqp_smoke_client.py", encoding="utf-8").read(), "amqp_smoke_client.py", "exec")'

USER 10001:10001
ENTRYPOINT ["python", "/app/amqp_smoke_client.py"]
