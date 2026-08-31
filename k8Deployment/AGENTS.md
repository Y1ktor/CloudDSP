# Kubernetes local-deployment instructions

## Scope

These instructions govern `k8Deployment/`, especially
`k8Deployment/kubernetes/`. This is a parallel local Kubernetes deployment
track. It must not replace, modify, or repurpose the AWS cloud deployment in
`../cloudDeployment/` unless the user explicitly requests a coordinated change.

Read [`../cloudDeployment/AGENTS.md`](../cloudDeployment/AGENTS.md) before
changing shared product behavior. Preserve its durable-job, security, media
limit, quota, retention, browser-performance, CSP, and ownership invariants in
the Kubernetes implementation. If a Kubernetes-specific public API, event,
data model, or infrastructure boundary changes, update `plan.md` and the
relevant K8 documentation in this tree.

## Teaching and task scope

This Kubernetes track is also a learning project. When creating or modifying
code, Helm charts, Kubernetes manifests, scripts, or configuration files, add
detailed inline documentation that explains the Kubernetes structure, the
responsibility of each resource, and the reason for important settings. For
example, explain non-obvious `apiVersion`, `kind`, metadata, selectors,
resources, security, storage, and controller choices. Do not add comments that
only repeat obvious syntax, and never place secrets in comments or examples.

Work on one small, concrete task at a time. Do not bundle several milestones
into one implementation turn. If the user request is generic, broad, or spans
multiple deliverables, stop before making changes and present:

1. A concise breakdown into small, independent tasks.
2. The task that should be completed first and why.
3. Any decision the user must make before that first task can start.

Wait for the user to select or provide the next specific task. Do not begin
later tasks merely because they appear in a plan.

## Layout and ownership

- Put all Kubernetes source under `kubernetes/`.
- Put reproducible k3d/bootstrap configuration in `kubernetes/cluster/`.
- Put Helm charts and values in `kubernetes/helm/`.
- Put K8-specific services and worker CLIs in `kubernetes/services/`.
- Put non-interactive build/deploy/teardown scripts in `kubernetes/scripts/`.
- Put unit, integration, and smoke tests in `kubernetes/tests/`.
- Pin reviewed image tags and digests in `kubernetes/images.lock.yaml`.

Do not add Kubernetes files to `../cloudDeployment/`, modify CloudFormation to
support local Kubernetes, or make K8 runtime code import AWS Lambda handlers.
Existing cloud entrypoints use Boto3, DynamoDB, S3, Lambda events, EventBridge,
and Batch; Kubernetes needs its own adapters and worker CLIs.

## Target platform

Use k3d for the standard local profile and native Linux x86_64 k3s with NVIDIA
GPU support for the GPU profile. Do not claim a Mac k3d cluster validates CUDA
or production-like GPU throughput.

Deploy only through versioned Helm configuration and non-interactive scripts.
Do not use ClickOps or uncommitted `kubectl` changes. Install KEDA before any
`ScaledJob` resources.

## Durable processing rules

- PostgreSQL is authoritative for jobs, artifact keys, revisions, UTC quotas,
  task leases, and outbox events.
- MinIO stores private object data only. Store stable keys rather than
  presigned URLs in PostgreSQL.
- RabbitMQ is at-least-once delivery. Use `(job_id, stage, stem_name)` as the
  idempotency key, manual acknowledgement after durable state, bounded retry,
  and DLQs.
- Persist artifacts before writing the matching outbox event. WebSocket updates
  are hints and browser polling remains required for correctness.
- A worker must not create another Kubernetes Job or use Kubernetes API
  permissions. It queues downstream work through durable messages.
- yt-dlp writes the ordinary upload key and follows the same intake-to-Demucs
  path as a direct browser upload.
- Preserve source types, byte and duration limits, owner checks, UTC quotas,
  retention, terminal deletion, and `job_id` correlation rules from the cloud
  deployment.

## Authentication, security, and containers

Use Keycloak plus PostgreSQL for local credentials. Use OIDC Authorization Code
with PKCE in the frontend and validate issuer, audience, expiry, signing keys,
and immutable `sub` in API and WebSocket services.

Never log access tokens, passwords, codes, presigned URLs, or credentials.
Keep MinIO private and browser access presigned. Use Kubernetes Secrets from
ignored local configuration; do not commit secrets or deployment-specific
values. Apply default-deny network policies and least-privilege service access.

Pin base images by digest. Build static frontend assets with Node and serve
them through NGINX. Keep workers single-purpose, resource-bounded, and free of
Lambda runtime assumptions. Demucs requests one GPU, uses GPU node affinity,
and is capped by real GPU capacity.

## Validation and Git workflow

Before marking a change complete, run relevant unit tests, frontend lint/build,
Helm lint/template, Kubernetes schema validation, `git diff --check`, and an
appropriate local smoke test. The smoke test covers authentication, upload,
terminal processing, MIDI, reconnect/poll recovery, failure/retry, duplicate
delivery, retention, and deletion.

Use Conventional Commits and keep cloud and Kubernetes changes separate unless
a deliberate shared product contract requires an explicitly coordinated change.
