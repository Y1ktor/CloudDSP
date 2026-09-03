# CloudDSP local Kubernetes deployment plan

## Scope and boundary

This is an additive, local Kubernetes deployment track. It does not replace,
migrate, or modify the AWS deployment preserved in
[`../cloudDeployment/`](../cloudDeployment/). The CloudFormation templates,
Lambda handlers, Batch entry points, Cognito integration, and cloud deployment
instructions remain authoritative for the cloud deployment.

Read [`../cloudDeployment/AGENTS.md`](../cloudDeployment/AGENTS.md) before
changing product behavior, source limits, job semantics, artifact retention,
browser behavior, or security policy. Kubernetes-specific code must live only
under `k8Deployment/kubernetes/`. Do not edit cloud source merely to make the
local deployment convenient unless the user explicitly requests a coordinated
change.

The local implementation will maintain equivalent product behavior while using
local deployment services. It is a parallel runtime, not a rewrite of the
preserved cloud source tree.

## Local target

Use **k3d** for the default local profile:

- Cluster name: `clouddsp-local`.
- Topology: one k3s server and two CPU agent nodes.
- Ingress: k3s-packaged Traefik.
- Registry: a k3d-managed local registry for CloudDSP images.
- Storage: local-path PersistentVolumeClaims for development-only state.

Use a separate native Linux x86_64 **k3s GPU profile** for real Demucs
throughput. That profile needs NVIDIA drivers, the NVIDIA GPU Operator or
device plugin, GPU-node labels and taints, and `nvidia.com/gpu` resource
requests. A Mac k3d cluster validates application integration and CPU workers;
it is not a validated CUDA/GPU capacity environment.

## Kubernetes workspace layout

```text
k8Deployment/
  AGENTS.md                 # Scoped instructions for this deployment track
  agent.md                  # Short entry point to AGENTS.md
  plan.md                   # This plan
  kubernetes/               # All future Kubernetes project source
    cluster/                # k3d configuration, namespaces, bootstrap files
    helm/                   # CloudDSP chart and local/GPU values
    services/               # K8-specific API, realtime, and worker code
      frontend/
      api/
      realtime/
      upload-intake/
      dispatcher/
      workers/
        demucs/
        basic-pitch/
        adtof/
        yt-dlp/
    scripts/                # Non-interactive build, deploy, and teardown
    tests/                  # Unit, integration, and smoke tests
    images.lock.yaml        # Reviewed tags and immutable image digests
```

`cloudDeployment/` and `k8Deployment/` are sibling, independently deployable
trees. Keep their package manifests, Dockerfiles, Helm resources, and runtime
configuration separate.

## Local services

| Component | Kubernetes responsibility | Local implementation |
| --- | --- | --- |
| Frontend | Static React delivery | Node build stage and NGINX Deployment behind Traefik |
| Identity | Local credentials and OIDC | Keycloak with a dedicated PostgreSQL database |
| Test email | Local SMTP capture and browser inbox | Mailpit; never an external mail relay |
| Job API | Authenticated job contracts and snapshots | K8-specific long-running service |
| Realtime | Best-effort WebSocket notifications | K8-specific realtime Deployment |
| Job state | Durable records, UTC quotas, revisions, and outbox | PostgreSQL |
| Artifacts | Private source, stems, MIDI, and tempo files | MinIO with presigned browser access |
| Work queue | Durable stage work, retry, DLQ, and status fanout | RabbitMQ quorum queues |
| Worker scaling | One worker Job per queued unit of work | KEDA `ScaledJob`s |
| GPU scheduling | Demucs resource isolation | Native Linux k3s GPU profile only |

Use locally owned CloudDSP images for all app services and workers. The
preserved Lambda images and Lambda handlers are cloud deployment artifacts;
they must not become Kubernetes entrypoints unchanged.

## Durable local processing path

```text
Browser ── OIDC ──> Keycloak
   │
   ├─ Job API ──> PostgreSQL (job, quota, task lease, outbox)
   └─ presigned POST ──> MinIO uploads/{job_id}/...
                               │
                    object-created event / reconciler
                               │
                     RabbitMQ demucs.requested
                               │
                    KEDA Demucs Job (GPU profile)
                               │
             MinIO stems + PostgreSQL state/outbox commit
                    ┌──────────┴──────────┐
                    │                     │
           Basic Pitch queue          ADTOF queue
                    │                     │
             KEDA CPU Job         KEDA CPU Job
                    └──────────┬──────────┘
                               │
                   MIDI + durable state/outbox
                               │
                       job.updated fanout
                               │
             realtime WebSocket hint; browser polls Job API
```

1. The local API validates the Keycloak subject, atomically reserves the UTC
   quota, creates the durable job, and returns a size-constrained MinIO
   presigned POST contract.
2. The upload-intake service validates the object and creates
   `demucs.requested`. A reconciler verifies pending uploads so a missed object
   event cannot strand work.
3. A Demucs worker claims its stage lease, validates source size, metadata,
   duration, and audio stream, then writes stable stem keys to MinIO.
4. After its artifact and PostgreSQL commit succeed, Demucs writes one outbox
   event per actual stem: drums route to ADTOF and pitched stems route to Basic
   Pitch.
5. KEDA creates independently retryable worker Jobs. Each persists MIDI and
   terminal state before acknowledging its RabbitMQ message.
6. A linked-media request first runs yt-dlp. Its normalized WAV enters the
   same ordinary upload-intake path; it never creates a second Demucs trigger.

Workers do not create Kubernetes Jobs or invoke other workers directly. They
use stable `(job_id, stage, stem_name)` idempotency keys, manual
acknowledgements after durable work, bounded retries, DLQs, and PostgreSQL
outbox publication. RabbitMQ and WebSockets are at-least-once/best-effort;
PostgreSQL is authoritative.

## Authentication and browser rules

- Keycloak owns password hashing, registration, confirmation, resets, and MFA
  in local PostgreSQL; application services must not own a password table.
- The local Keycloak issuer and browser entry point is
  `http://keycloak.localhost:8080`. Traefik routes that host only to Keycloak's
  application Service; never route its management health/metrics port.
- The local Mailpit browser inbox is `http://mailpit.localhost:8080`. Traefik
  routes that host only to Mailpit's web-UI Service; SMTP stays internal at
  `clouddsp-mailpit-smtp:1025` and must never receive an Ingress route.
- The local frontend uses OIDC Authorization Code with PKCE. API and WebSocket
  services validate issuer, audience, expiration, signing keys, and immutable
  `sub` ownership.
- Keep browser uploads presigned and private. Store object keys—not signed
  URLs—in durable state.
- Preserve browser polling as the artifact-retrieval fallback. WebSocket
  messages are hints only.
- Keep CSP exact. Add only reviewed local API, WSS, OIDC, and MinIO origins;
  never add broad origins or unsafe directives.

## Images and build policy

Start from reviewed upstream images and lock their digests in
`kubernetes/images.lock.yaml` before deployment:

| Purpose | Image basis |
| --- | --- |
| React build | `node:24-bookworm-slim` |
| React delivery | `nginx:1.28-alpine` |
| API, realtime, dispatchers | `python:3.12-slim-bookworm` |
| Identity | `quay.io/keycloak/keycloak` |
| Job and identity databases | `postgres:17-bookworm` |
| Object storage | `quay.io/minio/minio` |
| Queues | `rabbitmq:4-management` locally |
| Demucs | CloudDSP image based on `pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime` |
| Basic Pitch | CloudDSP image with the tested Basic Pitch runtime |
| ADTOF | CloudDSP image with pinned CPU PyTorch dependencies |
| yt-dlp | CloudDSP image with pinned yt-dlp, Deno, and FFmpeg |

Build frontend assets with Node and serve the immutable Vite output through
NGINX. Build `linux/amd64` GPU images only for the native Linux GPU profile.
Do not use floating image tags, random third-party worker images, Lambda bases,
or development servers as production-like runtime containers.

## Scaling, resilience, and security

- KEDA scales each worker stage independently from queue backlog. Cap Demucs
  replicas at actual GPU capacity and set `prefetch=1`.
- Give CPU workers independent CPU, memory, ephemeral-storage, deadline, and
  retry limits. Use HPA for API, realtime, and dispatcher Deployments.
- Apply `ResourceQuota`, `LimitRange`, `PriorityClass`, PodDisruptionBudgets,
  and default-deny `NetworkPolicy`.
- MinIO, PostgreSQL, and RabbitMQ use PVCs. Keycloak is a replaceable
  Deployment whose durable identity data lives in its dedicated PostgreSQL
  database. Local data is disposable only when explicitly destroyed; provide
  backup guidance before claiming high availability.
- Store credentials only in Kubernetes Secrets sourced from ignored local
  configuration. Never commit tokens, passwords, proxy addresses, presigned
  URLs, or live local data.
- Workers do not receive Kubernetes API credentials. MinIO, database, queue,
  and network access must be least-privilege.
- Preserve accepted media types, source byte/duration limits, host allowlists,
  per-user UTC quotas, terminal deletion rules, and 14-day retention behavior.

Kubernetes improves scheduling, isolation, retry handling, rollout control,
and stage-specific scale-out. It cannot provide high availability or GPUs
beyond the physical local nodes available to the cluster.

## Delivery phases

1. Scaffold the k3d cluster, local registry, namespaces, Helm chart, values,
   and ignored local configuration template.
2. Deploy PostgreSQL, MinIO, RabbitMQ, Keycloak, KEDA, ingress, and
   observability with persistent local storage.
3. Implement K8-specific API, realtime, upload-intake, PostgreSQL outbox, and
   Keycloak integration without touching the cloud source tree.
4. Build K8-specific worker CLIs and images for yt-dlp, Demucs, Basic Pitch,
   and ADTOF.
5. Add KEDA policies, GPU node profile, retention, terminal deletion, retry,
   DLQ, and duplicate-delivery recovery.
6. Run end-to-end and load tests before describing the local profile as
   high-workload ready.

## Required validation

Before Kubernetes changes are complete, run the relevant unit tests, frontend
lint/build, Helm lint/template, Kubernetes schema validation, `git diff
--check`, image/SBOM scanning, and an end-to-end local smoke test.

The smoke test must cover login, direct upload, linked ingestion, one terminal
processing job, MIDI availability, polling after a missed WebSocket update,
duplicate-message idempotency, retry/DLQ handling, retention, and terminal-job
deletion.
