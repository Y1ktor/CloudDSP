# Local CPU Demucs image recipe, version 1

This is the build decision for the first **local CPU** Demucs worker image.
The accompanying [`Dockerfile`](Dockerfile) has now been built and pushed to
the local registry. It remains neither a Kubernetes workload nor a running
worker: the AMQP consumer entrypoint and Deployment are later small tasks.

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
   secret, and accepts its PostgreSQL/RabbitMQ/MinIO configuration only through
   Kubernetes Secrets at Pod start.

The first local output has been built specifically for `linux/arm64`, pushed
to the k3d local registry, and pinned as
`images.demucs` in [`../../images.lock.yaml`](../../images.lock.yaml). Its
registry-confirmed digest is `sha256:9ff16a5a62ff0dfe615956cf606ab04231be7292ed50691193c0fb676299d165`;
the 479.50 MiB local image is not a CUDA image. An AMD64 CPU build or CUDA GPU
build is a later, separately locked artifact; neither is implied by the ARM64
digest.

## Follow-on tasks

1. Completed: `requirements.lock` contains the exact first-image Linux/ARM64
   Python runtime closure. A future x86 CPU/GPU task must extend it with its
   own native artifacts rather than reusing ARM64 hashes.
2. Completed: `model-artifacts.lock.yaml` fixes the required Demucs model
   artifacts and complete SHA-256 checksums.
3. Completed: [`Dockerfile`](Dockerfile) follows this recipe, including
   hash-locked dependencies, model-artifact verification, unit tests, FFprobe,
   and a final non-root image without a runtime entrypoint.
4. Completed: the ARM64 image was built, network-isolated/read-only verified,
   pushed to the k3d registry, and locked as `images.demucs` at 479.50 MiB.
5. Implement the long-running Demucs AMQP consumer entrypoint separately. It
   must use the existing parser, task lease, private MinIO, and FFprobe
   boundaries before it runs the model.
6. Define the worker Deployment separately; it will not be created by the
   image work.

## Primary references

- [Docker Official Python image](https://hub.docker.com/_/python)
- [PyTorch 2.4.0 installation matrix](https://pytorch.org/get-started/previous-versions/)
- [Demucs 4.0.1 release](https://pypi.org/project/demucs/4.0.1/)
- [Demucs v4 model documentation](https://github.com/facebookresearch/demucs)
- [Debian Bookworm FFmpeg package](https://packages.debian.org/bookworm/ffmpeg)
