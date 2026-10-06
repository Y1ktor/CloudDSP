# Local CPU Demucs image recipe and build history

The current [`Dockerfile`](Dockerfile) builds the Linux/ARM64 CPU worker;
[`images.demucs`](../../images.lock.yaml) records its deployable bytes, and the
[Helm release](../../helm/demucs/README.md) owns its running Deployment and scaler.
The original recipe and numbered milestones below preserve earlier build
decisions. Their tags, digests, and references to future work are historical.

## Current build and rollout: 2026-10-03

`0.1.10-lease-refactor` uses worker source commit `2334921` and splits task-lease
operations into focused modules while preserving SQL, transaction ownership,
recovery, and completion behavior. The 480.11 MiB runtime is pinned at
`sha256:6309797ec6911c5e4736efd1b8c6f1b10024e5d7021d679c1f82777db5e76fee`.
Its image validation passed 321 tests and real two-second, two-stem CPU
inference. Publication and verification passed for all 19 locked public
`y1ktor/clouddsp` images, including `demucs-0.1.10-lease-refactor`.

A normal Helm upgrade deployed chart `0.1.1` as Demucs release revision 3.
The worker Pod's image reference and runtime image ID matched the new digest;
Deployment, ScaledObject, and generated HPA UIDs were preserved.

The live smoke proved first-attempt Demucs completion, both private stem byte
hashes and metadata, two durable downstream events, and completed downstream
Basic Pitch tasks and parent Job. It cleaned only the exact fixture and removed
the disposable smoke Job. KEDA's ordinary five-minute warm cooldown follows
processing; this smoke result does not by itself assert the later idle state.
See the [dated rollout record](../../docs/trials/2026-10-03-frontend-demucs-refactor-rollout.md)
for the complete frontend and Demucs trial evidence.

## Why this is a different image from the cloud worker

The preserved cloud image starts from
`pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime`. That is appropriate for its
AWS Batch NVIDIA GPU runtime, but not for the current Apple-Silicon k3d
cluster. A Kubernetes Pod in k3d is a Linux/ARM64 container; it cannot use the
host's Metal/MPS device as a CUDA GPU. Carrying CUDA libraries would therefore
make the local image much larger without accelerating a single inference.

The local image will preserve the CloudDSP model choices and durable worker
contract, but will run PyTorch on the CPU. A later native Linux/AMD64 NVIDIA
profile will use a deliberately separate CUDA image recipe and manifest. It
must retain the same request, lease, MinIO, and PostgreSQL contracts; only its
execution platform and resource requests differ.

## Approved first-image recipe

| Layer | Pinned choice | Reason |
| --- | --- | --- |
| OCI base | `docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254` | Existing reviewed Docker Official Python 3.12.14 slim-bookworm multi-platform index. It selects Linux/ARM64 in the current k3d cluster and can select Linux/AMD64 for a future CPU build. |
| OS media tools | Debian Bookworm `ffmpeg=7:5.1.9-0+deb12u1` | Provides both `ffprobe` for the bounded preflight adapter and FFmpeg codecs. The future Dockerfile will request this exact Debian package version and fail rather than silently using a newer package. |
| Python | 3.12.14 | Matches the existing local API/worker base and is within the Demucs v4 supported Python range. |
| Demucs | `demucs==4.0.1` | Preserves the v4 behavior used by the existing cloud build before the later 4.1.0 release. It supports CloudDSP's `htdemucs` and experimental `htdemucs_6s` model names. |
| Tensor runtime | `torch==2.4.0` and `torchaudio==2.4.0` from PyTorch's CPU wheel index | Keeps the cloud worker's PyTorch 2.4.0 baseline while selecting no CUDA runtime. The completed initial lock records the exact Linux/ARM64 wheels; ARM64 CPU wheels do not use the `+cpu` filename suffix. A future x86 CPU/GPU task must lock its own compatible artifacts. |
| Device policy | Demucs invoked with `--device cpu`, one in-process separation at a time | Makes the no-CUDA local behavior explicit. A worker replica does not make an Apple GPU available, and `-j` would multiply memory use. |
| Image user | A dedicated non-root numeric user | Aligns with a future `runAsNonRoot`/read-only-root Pod. Only a size-limited `emptyDir` scratch mount will be writable. |

The image does **not** install `torchvision`: the worker does not use it.
It also does **not** carry a CUDA toolkit, NVIDIA driver, `torchcodec`, or
ambient AWS credential support. FFmpeg plus the pinned Demucs/Torch runtime is
the intentional media path; Boto3 will use the already restricted MinIO
credentials injected by Kubernetes.

## Model-weight rule

The cloud Dockerfile currently runs `get_model()` while building. That obtains
the named model from a remote mapping at build time, which is convenient but
does not identify the downloaded bytes in source control. The first local
image must not instead fetch model weights anonymously every time a Pod starts:
that would make normal processing depend on external internet access and would
break a read-only root filesystem.

[`model-artifacts.lock.yaml`](model-artifacts.lock.yaml) now records the exact
`htdemucs` and `htdemucs_6s` weight/descriptor files, their source URLs, byte
counts, complete SHA-256 checksums, and Demucs's shorter filename prefixes.
The Dockerfile sets `TORCH_HOME=/opt/clouddsp/demucs-models`, verifies each
recorded checksum before copying, and creates the two local descriptors for a
future `--repo` call. The initial ARM64 build verified all four locked model
artifacts before exporting its final image, so the image can run a future
Demucs separation without a Pod downloading model bytes from the internet.

## Required build shape

The Dockerfile uses two functional stages from the same immutable Python base:

1. **Validation stage:** install only the fully hash-locked dependency set,
   add the exact FFmpeg package, copy the worker source/tests, and run the
   existing isolated test suite. It will also assert that `ffprobe`, `torch`,
   `torchaudio`, and `demucs` import successfully and report their versions.
2. **Runtime stage:** copy only validated site packages, the checked worker
   source, FFmpeg runtime files, and separately verified model artifacts. It
   runs as the dedicated non-root user, exposes no HTTP port, contains no
   secret, accepts PostgreSQL/RabbitMQ/MinIO configuration only through
   Kubernetes Secrets at Pod start, and uses exec-form `python -m
   app.worker_main` as PID 1.

The historical `0.1.1-worker-entrypoint-local-only` output was built specifically
for `linux/arm64`, pushed to the k3d local registry, and recorded in the image
lock at that milestone. Its registry-confirmed OCI index digest was
`sha256:f8335a7a78108b74283d9d1d9fc46f82225d9dbe089b8fc961284c44da7f7db0`;
the 479.76 MiB local image is not a CUDA image. The earlier 479.50 MiB image
and its `9ff16a5…` digest predate the worker entrypoint and are superseded in
the catalog. An AMD64 CPU build or CUDA GPU build is a later, separately locked
artifact; neither is implied by the ARM64 digest.

## Historical first-image milestones

1. Completed: `requirements.lock` contains the exact first-image Linux/ARM64
   Python runtime closure. A future x86 CPU/GPU task must extend it with its
   own native artifacts rather than reusing ARM64 hashes.
2. Completed: `model-artifacts.lock.yaml` fixes the required Demucs model
   artifacts and complete SHA-256 checksums.
3. Completed: [`Dockerfile`](Dockerfile) follows this recipe, including
   hash-locked dependencies, model-artifact verification, unit tests, FFprobe,
   a final non-root image, and exec-form `python -m app.worker_main` as PID 1.
   It deliberately leaves `/worker-scratch` absent for the future bounded Pod
   `emptyDir` rather than falling back to the image filesystem.
4. Completed: the earlier ARM64 image was built, network-isolated/read-only
   verified, and pushed to the k3d registry at 479.50 MiB. It predates the
   reviewed worker entrypoint and is superseded in `images.demucs` by the
   current image below.
5. Completed: this reviewed source built locally for Linux/ARM64 as
   `clouddsp-demucs:0.1.1-worker-entrypoint-local-only`. Its validation stage
   passed all 296 worker tests, FFprobe, and exact Demucs/Torch/Torchaudio
   imports. The `503,066,464`-byte (`479.76 MiB`) runtime has the reviewed
   exec entrypoint, runs as UID `10003`, and leaves `/worker-scratch` absent;
   an offline default run without mounted configuration emitted only the safe
   wrapper diagnostic and exited `78`.
6. Completed: the exact local tag is pushed to the k3d registry as
   `clouddsp-registry.localhost:5001/demucs:0.1.1-worker-entrypoint-local-only`.
   Its registry-confirmed immutable OCI index reference is
   `clouddsp-registry.localhost:5001/demucs@sha256:f8335a7a78108b74283d9d1d9fc46f82225d9dbe089b8fc961284c44da7f7db0`.
   This push did not modify `images.lock.yaml` or create a workload.
7. Completed: `images.demucs` records that verified immutable image reference,
   its local size, CPU/ARM64 profile, and the exec-form worker entrypoint.
8. Completed: [`demucs-deployment.yaml`](demucs-deployment.yaml) prepares the
   first private, queue-driven worker controller. It uses only the immutable
   `images.demucs` digest, runs as UID/GID `10003` on ARM64 CPU nodes, mounts
   bounded disposable scratch volumes, has no Service/Ingress or Kubernetes
   API token, and receives only its three restricted runtime Secrets. Its
   intentionally omitted `replicas` field defaults to one only after an
   explicit apply and avoids a later conflict with KEDA's scale ownership.
9. Completed: the database/MinIO/RabbitMQ runtime Secret and identity preflight
   passed, then the Deployment was applied as its one normal local replica.
10. Completed: `0.1.2-short-lived-amqp-sessions-local-only` fixes the idle AMQP
    lifecycle by creating a new restricted session for each normal `basic_get`
    cycle and no AMQP session for PostgreSQL-only recovery. Its registry-
    confirmed immutable reference is
    `clouddsp-registry.localhost:5001/demucs@sha256:3c721bcad886969ecf2a31b70af1a92a7d2c7058c49abceae97faea0d5c88dd9`.
    The 479.77 MiB ARM64 runtime passed all 304 tests, FFprobe, and pinned ML
    imports before one ready, zero-restart rollout. RabbitMQ TCP `5672` remains
    an explicit outbound Service endpoint, so the worker intentionally has no
    `containerPort`, Service, or Ingress.
11. Completed: `0.1.3-recovery-uuid-join-fix-local-only` repairs
    the recovery-first expired-third-attempt terminalization scan. The prior
    query compared the text form returned for Python with the UUID `jobs`
    primary key, so PostgreSQL rejected `uuid = text` during planning before a
    normal AMQP poll could begin. The repair uses an explicit UUID cast and no
    longer returns `jobs.error_message`, preserving the Demucs role's narrow
    column-level read authority. The Linux/ARM64 CPU image passed all 304
    tests, FFprobe, and pinned runtime imports, and was pushed as
    `clouddsp-registry.localhost:5001/demucs@sha256:99e24301c4c2f37547de7fd8a7773d7e0eae6cfb5f4222db9e00ae1294cae73a`
    at 479.77 MiB. `images.demucs` and the Deployment are pinned to it, and a
    one-ready, zero-restart rollout completed. The failed smoke run's exact
    object/Job/outbox evidence was then removed through the separately
    reviewed cleanup Job; the next smoke run remains an explicit action.
12. Prepared: `0.1.4-local-arm64-mkldnn-sigill-fix` addresses the next
    deployed-smoke finding. The actual fixed Demucs CPU command reached a
    Linux/ARM64 Torch model kernel that ended with exit code 132 (`SIGILL`)
    under the local Docker Desktop/k3d virtual CPU. This was not a broker,
    database, MinIO, source-upload, model-weight, or task-lease failure: a
    direct repeat of the same input wrote both expected stems once
    `torch.backends.mkldnn.enabled` was set to `False` before importing
    `demucs.separate`. The worker now starts its fixed child through
    `python -m app.processing.demucs_cpu_cli`, which applies and reads back that one
    child-process-only setting before it loads Demucs. A future NVIDIA/GPU
    image must keep a separately validated backend policy rather than copying
    this local CPU workaround. The image validation stage now generates a
    two-second WAV and proves a real `htdemucs --two-stems vocals` run writes
    `vocals.wav` and `no_vocals.wav`. All 309 source tests, FFprobe, locked ML
    imports, and that inference check passed. The resulting 479.77 MiB image
    is pinned for a separate rollout as
    `clouddsp-registry.localhost:5001/demucs@sha256:d0ebd533f66eb2c8097646477a6ba1439349add297bfde578e4a865f08c9186b`.
13. Prepared: `0.1.5-local-arm64-launcher-cwd-fix` corrects a separate
    runtime-startup defect discovered by the subsequent live smoke. The
    process adapter intentionally invokes each model child with its fresh
    output directory as `cwd`; therefore the earlier module invocation could
    not resolve the image's `/app` package. This revision invokes the same
    reviewed launcher as `/usr/local/bin/python /app/app/processing/demucs_cpu_cli.py`,
    preserving the child-only MKLDNN safeguard without a Pod-wide
    `PYTHONPATH`. Docker now changes into the same output-directory context
    before its real two-second two-stem inference. All 309 source tests,
    FFprobe, locked ML imports, and that exact-context inference passed. The
    resulting 479.77 MiB image is pinned for an explicit future rollout as
    `clouddsp-registry.localhost:5001/demucs@sha256:43b4c352a3c4bf077fe685a0904d368f5cb89c2f40a25b1b2a006045b2ec231c`.
14. Prepared: `0.1.6-local-recovery-verifier-fix` replaces the worker's direct
    recovery read of `outbox_events.payload` with a PostgreSQL
    security-definer boolean verifier. The restricted Demucs database role
    remains unable to read arbitrary payloads, but can safely reconstruct a
    task only after PostgreSQL proves that its fresh recovery lease and the
    immutable published v1 request match. The Docker validation stage now
    includes the compatibility bootstrap manifest and its least-privilege
    structural test. All 311 source tests, FFprobe, locked runtime imports,
    and real two-stem inference passed. The resulting 479.77 MiB image is
    pinned for an explicit future rollout as
    `clouddsp-registry.localhost:5001/demucs@sha256:0732ea521f20fe5b64136c1bfdcba867cae909979e082b8c5a267adc56256254`.

## Primary references

- [Docker Official Python image](https://hub.docker.com/_/python)
- [PyTorch 2.4.0 installation matrix](https://pytorch.org/get-started/previous-versions/)
- [Demucs 4.0.1 release](https://pypi.org/project/demucs/4.0.1/)
- [Demucs v4 model documentation](https://github.com/facebookresearch/demucs)
- [Debian Bookworm FFmpeg package](https://packages.debian.org/bookworm/ffmpeg)
