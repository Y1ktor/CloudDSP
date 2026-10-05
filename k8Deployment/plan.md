# CloudDSP local Kubernetes architecture and status

This is the current implementation summary, reviewed against the versioned
source on **2026-10-04**. For prerequisites, configuration, deployment,
verification, and cleanup, use the [operator guide](kubernetes/scripts/README.md)
and the [repository README](../README.md). The complete incremental implementation
record is preserved in [history](kubernetes/docs/history/2026-10-03-kubernetes-implementation-log.md).
Historical trial results and resource identities do not establish the state of
your running cluster; use the versioned verification command for that.

## Source boundaries

The AWS and Kubernetes backends remain separate. CloudFormation, Lambda,
Batch, Cognito integration, and cloud deployment settings belong to
[`cloudDeployment/`](../cloudDeployment/). Kubernetes APIs, worker CLIs, Helm
charts, bootstrap scripts, and runtime settings belong to this deployment tree.
Local backend code does not import cloud Lambda handlers.

The canonical React application is shared in [`frontend/`](../frontend/).
Common screens, editor components, audio/MIDI hooks, assets, and dependencies
have one source. Build-selected adapters in `frontend/src/platform/` preserve
Cognito/AWS integration for the cloud profile and Keycloak/local-service
integration for the local profile. The local Dockerfile and NGINX delivery
configuration remain in [`services/frontend/`](kubernetes/services/frontend/README.md).
The unused EQ prototypes are retained separately in [`archive/eq/`](../archive/eq/README.md).

```text
frontend/                         # Shared React application
  src/platform/cloud/             # Cognito and cloud browser integration
  src/platform/local/             # Keycloak and local browser integration
  profiles/                       # Public configuration examples
k8Deployment/
  .local/                         # Ignored credentials and local configuration
  kubernetes/
    cluster/                      # Versioned k3d topology and namespaces
    helm/                         # Component charts and reviewed values
    credentials/                  # Value-free credential catalog and overrides
    services/                     # Local API, intake, dispatchers, workers, delivery
      job-api/                    # Authenticated HTTP Job API
      demucs/app/                 # Grouped worker modules; same layout for MIDI workers
        db/                       # Task ownership, leases, and result transactions
        artifacts/                # Object coordinates, downloads, and storage proof
        messaging/                # Request parsing, broker sessions, ACK/NACK
        processing/               # Model invocation and media/artifact validation
        runtime/                  # Execution orchestration, recovery, shutdown
    scripts/
      deploy-local.sh             # Public deployment/lifecycle entry point
      orchestration/              # Root coordinators and stage order
      releases/                   # Component Helm commands
      stages/                     # Credentials, database, MinIO, RabbitMQ, Keycloak
      images/                     # Builders and registry mirroring/checks
      maintenance/                # Cleanup and opt-in repair/restore tools
      lib/                        # Shared helpers and path resolution
    tests/                        # Unit, integration, and opt-in smoke/load checks
    docs/history/                 # Preserved implementation/adoption records
    docs/trials/                  # Dated VM trial evidence
    images.lock.yaml              # Reviewed image references and build provenance
```

Worker images keep `python -m app.worker_main` as their public launcher. It
delegates to `app.runtime.worker_main`; internal package paths are implementation
details. Chart commands and existing locked images retain that launch contract.
See the [script layout](kubernetes/scripts/README.md#script-layout) for focused
helper paths.

## Local platform

The standard profile creates `clouddsp-local` through k3d, with Kubernetes
context `k3d-clouddsp-local`: one K3s server and two CPU agents. The
[cluster definition](kubernetes/cluster/k3d.yaml) pins K3s, loopback API and
ingress ports, and the dedicated local registry. Published application images
currently target Linux ARM64; this profile has been trialed on Apple Silicon
macOS and Ubuntu ARM64. A native Linux/NVIDIA GPU profile remains separate
work and is not validated by the Mac CPU cluster.

| Namespace | Responsibility |
| --- | --- |
| `clouddsp-app` | Frontend, Job API, upload-intake, both dispatchers, Demucs, Basic Pitch, ADTOF, and worker scaling authentication. |
| `clouddsp-data` | PostgreSQL, MinIO, RabbitMQ, Keycloak, Mailpit, and service bootstrap resources. |
| `clouddsp-system` | Reserved CloudDSP platform namespace; not the K3s system namespace. |
| `keda` | Pinned upstream KEDA Helm release. |
| `kube-system` | K3s-managed DNS, Traefik, networking, metrics, and local-path storage components. |

Application access uses `http://clouddsp.localhost:8080`, Keycloak uses
`http://keycloak.localhost:8080`, and Mailpit uses
`http://mailpit.localhost:8080`. These are loopback HTTP development routes.
PVCs use node-local storage; the profile provides neither production
availability nor automatic application-data recovery.

## Implemented processing path

```text
Browser -> Keycloak Authorization Code + PKCE
Browser -> Job API -> PostgreSQL upload intent
Browser -> private MinIO presigned POST
MinIO ObjectCreated -> RabbitMQ source queue -> upload-intake
upload-intake -> PostgreSQL source transition + transactional outbox
dispatcher -> RabbitMQ demucs.requested -> Demucs CPU worker
Demucs -> private stem objects + PostgreSQL downstream outbox
generic dispatcher -> Basic Pitch / ADTOF queues -> CPU MIDI workers
MIDI workers -> private MIDI/tempo objects + guarded PostgreSQL completion
Browser -> authenticated Job API polling -> fresh presigned artifact URLs
```

PostgreSQL is authoritative for jobs, artifact keys, revisions, processing
tasks, leases, retries, and outbox state. RabbitMQ delivery can duplicate;
workers use durable `(job_id, stage, stem_name)` identity and guarded
transitions to classify duplicate or stale work. A worker acknowledges after
a committed task claim; database lease recovery handles a Pod loss after that
acknowledgement. Artifact writes precede the matching durable completion or
outbox update. No worker creates another Kubernetes Job.

The schema runner maintains the immutable `v001`–`v009` migration ledger.
Fresh bootstrap applies through `v006`, creates the Basic Pitch and ADTOF
roles, then applies the remaining migrations. Job finalization and tempo
projection are implemented through the later PostgreSQL functions/triggers;
a partial downstream task inventory waits for asynchronous dispatch rather
than prematurely failing its parent job. See the
[migration runner](kubernetes/scripts/stages/database/job-api-migrations.rb) and
[Job API implementation](kubernetes/services/job-api/README.md).

Both dispatcher releases remain in the bootstrap: the legacy release handles
Demucs requests, while the generic release routes stage requests. KEDA uses
`ScaledObject` resources for long-running worker Deployments. Zero idle
worker Pods is expected. Demucs and Basic Pitch scaling observe both broker
backlog and durable PostgreSQL tasks because broker acknowledgement precedes
processing. ADTOF currently scales only on broker depth; its running-Pod lease
recovery cannot independently wake the Deployment from zero for acknowledged
work. See the [ADTOF recovery limitation](kubernetes/services/adtof/README.md#security-and-current-scaling-limitation).

## Authentication and security boundaries

Keycloak owns local passwords, registration, email verification, and recovery;
Mailpit captures local email. The SPA is a public OIDC client using S256 PKCE
and OAuth access tokens. The API validates signature, issuer, audience,
expiry, and immutable `sub` ownership. Cloud builds retain their Cognito ID
token contract instead.

The Job API issues constrained presigned upload forms and fresh authorized
artifact downloads. User audio, stems, MIDI, and tempo objects stay in private
`clouddsp-uploads`. The separate `clouddsp-midi-samples` bucket holds reviewed
shared instrument sounds, with anonymous object reads only and no listing or
write grant. Bootstrap mirrors and checks the locked sample set.

The value-free [credential catalog](kubernetes/credentials/catalog.yaml)
generates 33 owner-only Secret source files under ignored `.local/`, optionally
using user overrides. Runtime charts refer to Secret names; populated values
are not tracked in Git or embedded in React. Bootstrap identities and runtime
identities have separate lifecycles. Workloads use restricted database,
RabbitMQ, and MinIO grants, reviewed security contexts, bounded resources, and
no worker Kubernetes API credentials. RabbitMQ has scoped ingress rules;
namespace separation alone is not a claim of complete network isolation.

## Deployment lifecycle

`bootstrap-platform` is the completed fresh-install command. The checked-in
[platform coordinator](kubernetes/scripts/orchestration/deploy-local-platform.rb) lists
**93 ordered stages**: absent-cluster guard and credentials, foundation/image
mirror, data services, bucket/sample/IAM setup, Keycloak, schema and service
identities, broker topology, KEDA, application/worker releases, and frontend.
Use `deploy-local.sh stages` to get the exact current list from source.

The fresh path requires an absent target cluster and stops at the first failed
stage, preserving partial resources for inspection. `verify` runs read-only
checks of the cluster, images, Helm resources, identities, schema, and external
service configuration. `reconcile` repairs only its audited bootstrap state;
existing Helm releases must already match their charts. It is not a general
Helm upgrade or data-restoration command.

`cleanup` removes the cluster and Kubernetes resources, including database and
MinIO PVC data, while retaining the dedicated registry and `.local/` files.
`purge-registry` is a separate operation after cluster cleanup. A fresh machine
mirrors reviewed Docker Hub images into that registry; a later fresh cluster
can reuse the retained images. Neither command restores earlier user data.

See the [current ownership map](kubernetes/resource-ownership-map.md) for
resource lifecycles and the [operator guide](kubernetes/scripts/README.md) for
the exact commands. The [VM trials](kubernetes/docs/trials/) record successful
fresh bootstrap and same-VM redeployment for their named source revisions;
end-to-end smoke tests remain distinct from read-only verification.

## Optional Flux reconciliation

Flux v2.9.6 is an opt-in bootstrap for the existing local cluster, configured
on `codex/flux-clouddsp-local`. It reads the public repository over HTTPS
without a GitHub credential in Kubernetes. The explicit root selects Flux
and six native application releases: Mailpit, frontend, Job API,
upload-intake, generic-dispatcher, and the legacy Demucs-only dispatcher. Mailpit's target/storage remain `clouddsp-data`; the other
five releases remain in `clouddsp-app`, preserving their original names.
Separate namespace-scoped identities perform Helm actions, with Git revision
packaging and drift detection enabled. Base charts, values, locked images,
Pod templates, and Secret references are unchanged by these handoffs.

Upload-intake's HelmRelease waits for the already managed Job API release.
The other required database, broker, MinIO, and Keycloak bootstrap state
retains its script ownership; a readiness dependency does not provision
those identities or service settings. Upload-intake's chart owns only its
outbound Deployment and grants no Service, Ingress, or test Job permissions.
Host cluster/registry lifecycle and durable service contents remain separate.

Each new chart source is prepared and its archive verified before enabling
its HelmRelease in the root. This avoids sparse-source packaging races.
Versioned configuration establishes the intended reconciliation scope; check
current-generation HelmRelease readiness and the release helpers on a running
cluster. These helpers support direct Helm and Flux layouts, retain strict
read-only verification, and block direct install/adopt while the matching
HelmRelease exists, including failed, suspended, and deleting states.

Mailpit retains SMTP smoke testing. Frontend verifies health, shell, CSP,
JavaScript/CSS, and direct SPA routes. Job API verifies the running digest and
protected routes; its disposable authenticated smoke checks real Keycloak
tokens and the PostgreSQL-backed owner job list. Upload-intake verifies its
running digest and Ready Pod, with a separate source-to-outbox smoke covering
authenticated upload, native MinIO notifications, atomic outbox state, and
duplicate delivery. That smoke stages both publishers' pause/restore through a shared editor and
committed Flux valuesFiles. Each read-only pause verifier requires every
publisher Pod, including terminating ones, to disappear.
The generic routing smoke temporarily pauses the native Basic Pitch scaler
through a reviewed maintenance values file, checks an exact synthetic delivery,
and restores normal scaling after cleanup.

Operating guides: [Mailpit](kubernetes/gitops/mailpit.md),
[frontend](kubernetes/gitops/frontend.md), [Job API](kubernetes/gitops/job-api.md),
[upload-intake](kubernetes/gitops/upload-intake.md),
[generic-dispatcher](kubernetes/gitops/generic-dispatcher.md), and
[legacy dispatcher](kubernetes/gitops/dispatcher.md). Source is maintained in
the [dedicated branch](https://github.com/Y1ktor/CloudDSP/tree/codex/flux-clouddsp-local/k8Deployment/kubernetes/gitops).
Cluster cleanup removes Flux too; ordinary fresh deployment still provisions
all dependencies before the separate opt-in Flux bootstrap.

## Remaining product scope

The implemented local API has authenticated identity, job creation through
direct upload, saved-job listing, and job-detail polling. A local realtime
service, linked-media ingestion, terminal-job deletion route, and cloud-style
daily submission quotas and scheduled retention cleanup are not yet implemented
in that API/deployment. Shared frontend
controls do not establish backend support for these features. A production
TLS/availability profile, data backup/restore workflow, and NVIDIA GPU profile
also remain separate work. Preserve the cloud product/security invariants
when implementing that parity; consult [AGENTS.md](AGENTS.md) and the
[cloud architecture](../cloudDeployment/docs/architecture.md).
