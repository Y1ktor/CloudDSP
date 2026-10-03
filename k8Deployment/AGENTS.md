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

The shared React application in [`../frontend/`](../frontend/) is an explicit
exception to the deployment-directory boundary. Its screens, audio/MIDI hooks,
assets, package manifest, and lockfile are canonical for both deployments.
Adapters under `../frontend/src/platform/` select the Cognito/AWS cloud profile
or the Keycloak/local-service profile at build time. Keep local containers,
NGINX configuration, and Kubernetes resources under this deployment tree.
Shared UI changes require both frontend profiles to pass validation; local
backend code must still remain independent of AWS handlers.

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

- Put Kubernetes infrastructure and backend source under `kubernetes/`.
- Put common browser source in `../frontend/`, using its build-selected
  platform adapters for deployment-specific browser behavior. Do not create
  another React source copy under `kubernetes/services/frontend/`.
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

The implemented standard local profile uses k3d with ARM64 CPU images.
Native Linux x86_64 k3s with NVIDIA GPU support is a separate future GPU
profile, requiring its own reviewed images and scheduling configuration. Do
not claim a Mac k3d cluster validates CUDA or production-like GPU throughput.

Deploy only through versioned Helm configuration and non-interactive scripts.
Do not use ClickOps or uncommitted `kubectl` changes. Install KEDA before any
`ScaledObject` resources.

## Durable processing rules

- PostgreSQL is authoritative for jobs, artifact keys, revisions, task leases,
  and outbox events. Future UTC quota enforcement must use the same durable
  boundary; it is not yet implemented locally.
- MinIO job artifacts stay private. Store stable keys rather than presigned
  URLs in PostgreSQL. The separate `clouddsp-midi-samples` bucket is a narrow
  exception for shared, non-user instrument sounds: anonymous `GetObject`
  only, with no bucket listing or write grant. Never put user data there.
- RabbitMQ is at-least-once delivery. Use `(job_id, stage, stem_name)` as the
  idempotency key, manual acknowledgement after durable state, bounded retry,
  and DLQs.
- Persist artifacts before writing the matching outbox event. WebSocket updates
  are hints and browser polling remains required for correctness.
- A worker must not create another Kubernetes Job or use Kubernetes API
  permissions. It queues downstream work through durable messages.
- When linked-media ingestion is added, yt-dlp must write the ordinary upload
  key and follow the same intake-to-Demucs path as a direct browser upload.
- Preserve source types, byte and duration limits, owner checks, UTC quotas,
  retention, terminal deletion, and `job_id` correlation rules from the cloud
  deployment. These are target parity requirements; current local API gaps
  are listed in `kubernetes/services/api/README.md`. Do not describe an
  unimplemented route, quota, or cleanup worker as a deployed capability.

## Authentication, security, and containers

Use Keycloak plus PostgreSQL for local credentials. Use OIDC Authorization Code
with PKCE in the frontend and validate issuer, audience, expiry, signing keys,
and immutable `sub` in the API. A future realtime service must apply the
same validation; the current local frontend recovers state through polling.

Never log access tokens, passwords, codes, presigned URLs, or credentials.
Keep user-data MinIO buckets private and browser job-artifact access presigned.
The read-only shared sample bucket above may be fetched directly by the local
browser; its bootstrap must not expose administrator credentials to React.
Use Kubernetes Secrets from ignored local configuration; do not commit secrets
or deployment-specific values. Apply default-deny network policies and
least-privilege service access.

Pin base images by digest. Build static frontend assets with Node and serve
them through NGINX. Keep workers single-purpose, resource-bounded, and free of
Lambda runtime assumptions. Standard local Demucs uses CPU. A future GPU
profile must request a GPU, use appropriate GPU node affinity, and cap scaling
by real GPU capacity.

## Validation and Git workflow

Before marking a change complete, run relevant unit tests, frontend lint/build,
Helm lint/template, Kubernetes schema validation, `git diff --check`, and an
appropriate local smoke test for the changed implemented boundary. Processing
checks cover authentication, upload, terminal processing, MIDI, polling
recovery, failure/retry, and duplicate delivery as relevant. Add realtime
reconnect, quota, retention-cleanup, and deletion checks when those local
features are implemented. For documentation-only changes, validate links,
commands, source consistency, and whitespace; no live deployment is required.

Run shared browser checks from `../frontend/`: `npm run lint`, `npm test`,
`npm run build:cloud`, and `npm run build:local`. Cloud mode writes
`dist/cloud/`; Vite mode `k8` writes `dist/local/`. For development use the
public examples in `profiles/`; the local Docker image builder reads public
settings from ignored `.local/frontend.env.production` in this deployment
tree. Never use that file for server-side secrets.

Use Conventional Commits and keep cloud and Kubernetes changes separate unless
a deliberate shared product contract requires an explicitly coordinated change.
