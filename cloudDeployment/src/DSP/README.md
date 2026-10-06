# Cloud processing source and image builds

This tree contains the active AWS processing backend. The shared browser app is
in [the root frontend](../../../frontend/README.md); the Kubernetes backend is
in [the local deployment tree](../../../k8Deployment/plan.md).

## Active layout

```text
src/DSP/
  cloud/                 # Active Lambda handlers, Batch entry point, helpers
    lambdazip/           # Retained ZIP release artifacts; packaged separately
  docker/
    stem_split/          # AWS Batch GPU Demucs image
    basic_pitch/         # Basic Pitch Lambda image and custom-runtime bootstrap
    adtof/               # ADTOF Lambda image
    yt-dlp/              # Linked-media ingestion Lambda image and requirements
  tests/                 # Automated quota and media URL-policy tests
  .dockerignore          # Excludes host environments, outputs, and ZIP releases
```

Each active image directory has one `Dockerfile`. Basic Pitch's default file is
the Lambda recipe formerly named `Dockerfile.lambda`; its container handler
remains `BasicPitch.lambda_handler`. The older TensorFlow/Batch recipe is
retained in [the prototype archive](../../../archive/dsp/README.md).

| Source in `cloud/` | Deployment responsibility |
| --- | --- |
| `job_api.py`, `media_url_policy.py` | Authenticated jobs, owner checks, submission quotas, artifact URLs, and linked-source URL policy. |
| `websocket_handler.py`, `websocket_authorizer.py` | Authenticated subscriptions, heartbeat lifecycle, and Cognito token validation. |
| `BatchDemucs.py` | GPU stem separation and Basic Pitch/ADTOF handoff. |
| `LambdaMIDIBasicPitch.py`, `LambdaMIDIADTOF.py` | Pitched-stem and drum MIDI extraction. |
| `LambdaYtDlp.py` | Validated linked-media ingestion into the ordinary upload path. |
| `cloud_job_workflow.py` | Durable state transitions and notifications shared by processing images. |

Source folder names do not determine Lambda handler module names. Dockerfiles
keep the runtime copy destinations and `CMD`/`ENTRYPOINT` contracts. ZIP Lambda
packages still place their handlers and dependencies at the package root. A
folder move does not replace a deployed Lambda, image, or S3 release object.

## Build context

Use `cloudDeployment/src/DSP` as the context for every active image. For example,
from the repository root:

```bash
docker buildx build --platform linux/amd64 \
  --file cloudDeployment/src/DSP/docker/basic_pitch/Dockerfile \
  --tag clouddsp-basic-pitch:local --load cloudDeployment/src/DSP
```

Basic Pitch, ADTOF, and yt-dlp use the cloud Lambda x86_64 profile. Demucs retains
its AWS Batch CUDA image; local Kubernetes ARM64 CPU recipes are separate.
Publishing and CloudFormation updates follow [the cloud instructions](../../AGENTS.md#3-local-setup-and-validation).

The build context excludes local virtual environments, audio output, tests,
sample assets, and ZIP releases. Retained experiments live outside this context
in [the DSP archive](../../../archive/dsp/README.md). Active source must not import
archived handlers or copy them into a release.

## Validate

Run these offline suites with the cloud development environment activated:

```bash
python3 -m unittest discover --start-directory cloudDeployment/src/DSP/tests --verbose
```

The tests use fake AWS credentials and mocked clients. The manual audio-effect
examples now live in the archive and are separate from this automated suite.
