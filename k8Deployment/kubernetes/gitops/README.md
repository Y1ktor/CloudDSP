# Flux bootstrap for the local CloudDSP cluster

This opt-in bootstrap installs **Flux v2.9.6** in `flux-system` and connects
it to the public CloudDSP GitHub repository. It manages its own installation
and selects fifteen native Helm releases: KEDA, Keycloak, MinIO, PostgreSQL, RabbitMQ, Mailpit, frontend, Job API, upload-intake,
generic-dispatcher, the legacy Demucs-only dispatcher, shared scaling-auth, and
the ADTOF, Basic Pitch, and Demucs workers.
Cluster and registry provisioning, credential generation, and application
bootstrap state retain their existing script ownership. The local cluster can
run without Flux; this handoff adds continuous reconciliation from published
Git commits. See the [Mailpit handoff guide](mailpit.md),
[frontend handoff guide](frontend.md), [Job API handoff guide](job-api.md),
[upload-intake handoff guide](upload-intake.md),
[generic dispatcher guide](generic-dispatcher.md),
[legacy dispatcher guide](dispatcher.md),
[scaling authentication guide](scaling-auth.md), [ADTOF guide](adtof.md),
[Basic Pitch guide](basic-pitch.md), [Demucs guide](demucs.md),
[KEDA platform guide](keda.md), [RabbitMQ guide](rabbitmq.md),
[PostgreSQL guide](postgresql.md), [MinIO guide](minio.md), and
[Keycloak guide](keycloak.md) for source, verification, and
recovery rules. These source definitions do not establish the current live handoff state;
check each HelmRelease and its release helper before relying on adoption.

## Git and cluster boundaries

| Setting | Current configuration |
| --- | --- |
| Kubernetes context | `k3d-clouddsp-local` |
| Git repository | `https://github.com/Y1ktor/CloudDSP.git` |
| GitOps branch | `main` |
| Reconciled directory | `k8Deployment/kubernetes/gitops/clusters/clouddsp-local` |
| Git poll and root reconciliation interval | One minute |

The `main` branch carries the canonical
[shared frontend](../../../frontend/), current [Helm charts](../helm/),
[image lock](../images.lock.yaml), and organized [deployment helpers](../scripts/).
The explicit root
configures reconciliation for Flux, KEDA, Keycloak, MinIO, PostgreSQL, RabbitMQ, Mailpit, frontend, Job API, upload-intake,
generic-dispatcher, the legacy dispatcher, scaling-auth, ADTOF, Basic Pitch, and Demucs.
Flux fetches the published repository, so a local commit must be pushed before
the cluster can see it. Changes on another branch are deployed only after they
reach the configured branch, normally through a merged pull request.

Every cluster configured to follow CloudDSP's `main` can receive merged changes
to its selected manifests and charts. Each cluster's Flux controllers fetch and
apply those changes using their own Kubernetes credentials. Cloning this
repository or installing the Flux CLI alone does not connect a cluster to it.
For independent updates, use a fork as described below; upstream changes reach
that cluster only after they are merged into the fork's watched branch.

`GitRepository` is a Flux API resource: source-controller downloads its branch.
The Flux `Kustomization` API resource selects the directory to apply. The
`kustomization.yaml` files are Kustomize build inputs, with explicit resource
lists so retained raw service manifests, one-time Jobs, tests, and credentials
are not recursively adopted. The source uses sparse checkout for the GitOps
directory and fourteen application chart directories listed in
[`gotk-sync.yaml`](clusters/clouddsp-local/flux-system/gotk-sync.yaml).
KEDA uses the official HelmRepository and an exact upstream chart version; its public inline
values must match the exact transformation of `helm/keda/values.yaml` described
in its guide: omit one duplicate upstream label input and preserve its effective
value through explicit postrenderer patches. An additional component needs
a reviewed HelmRelease, suitable reconciliation RBAC, its required chart source
paths, and an explicit entry in the cluster root as
part of its ownership handoff. Expand and verify the source artifact before
enabling its HelmRelease so packaging can find the chart. Source files alone
do not add a release to Flux reconciliation.

## Controllers and security

The generated `gotk-components.yaml` contains the official, version-pinned
installation: CRDs, Services, service accounts, RBAC, network policies, and
four Deployments. Regenerate that file using the command below; do not edit
its generated internals.

- **source-controller** fetches Git and other sources and serves their artifacts.
- **kustomize-controller** applies declarative resource sets and reports health.
- **helm-controller** manages the fifteen explicitly selected native
  Helm releases. Fourteen application identities use namespaced Roles; KEDA delivery
  also needs named cluster resources and explicit RBAC bind/escalate rights.
- **notification-controller** provides optional alerts and webhook receivers.

The cluster fetches public Git over HTTPS without a credential Secret. Host SSH
authentication is used to publish Git commits and is not copied into Kubernetes.
Changing the repository to private requires a dedicated, read-only credential.
The standard controllers run as non-root, disable privilege escalation, drop
Linux capabilities, and use read-only root filesystems. Generated network
policies allow controller communication and egress; metrics port 8080 and
notification webhook port 9292 have explicit ingress exceptions.

The installation grants the reconcilers cluster-wide administration. Restricting
the Git path limits selected manifests; it is not an RBAC boundary. Trusted
writers to this branch can change the cluster through reviewed manifests.
Each selected release's Helm actions impersonate a separate service account bound
into their existing release namespaces. Application Roles grant only chart API kinds
and read-only readiness checks, plus namespace-wide Secret access required by
Helm revision storage. The root controller can still change this RBAC; trusted
Git writers remain responsible for reviewing handoffs. KEDA's separate platform
identity can alter its powerful runtime roles and controller Pods; it is trusted
platform administration. It grants no CRD deletion or direct app/data Secret
access. See its guide for the complete permission and TLS/CRD boundaries.

`prune: true` removes previously managed objects when removed from Git. The
root inventory includes Flux, each selected release's reconciliation RBAC, and
the fifteen selected HelmReleases and KEDA's official HelmRepository. Workload objects and Helm revision Secrets
belong to their native Helm releases. Removing an active HelmRelease normally
uninstalls that release; treat its removal as a deliberate cleanup operation.
Deleting the entire k3d cluster still removes Flux;
the normal host bootstrap must recreate the cluster before this optional
bootstrap is run again. `k3d cluster stop/start` pauses and resumes controllers
along with the other workloads.

## Bootstrap

Prerequisites: an existing supported CloudDSP cluster with `clouddsp-data`
and `clouddsp-app`, verified native `clouddsp-mailpit`, `clouddsp-frontend`,
`clouddsp-job-api`, `clouddsp-upload-intake`, `clouddsp-generic-dispatcher`,
`clouddsp-dispatcher`, `clouddsp-scaling-auth`, `clouddsp-adtof`, and
`clouddsp-basic-pitch`, `clouddsp-demucs`, `clouddsp-keycloak`, `clouddsp-minio`, `clouddsp-postgresql`, and `clouddsp-rabbitmq`
releases for adoption, cluster-admin kubeconfig access, `kubectl`, `git`, and
the Flux CLI (`brew install fluxcd/tap/flux` on macOS). Frontend's Keycloak
configuration, Job API, static image, registry, and browser origin must already
be ready through the ordinary platform stages. Keycloak waits for PostgreSQL
and Mailpit; the frontend waits for Keycloak readiness.
MinIO depends on RabbitMQ readiness. Job API depends on Keycloak, PostgreSQL and MinIO.
Upload-intake depends on PostgreSQL, MinIO, Job API and RabbitMQ; both dispatchers
depend on PostgreSQL, Job API and RabbitMQ HelmRelease readiness. The three
workers depend on MinIO and shared scaling-auth. Its database,
broker, and MinIO identities/notification state must already be provisioned by
the ordinary bootstrap; its source-to-outbox smoke remains a separate runbook.
KEDA adoption requires its existing pinned release/controllers/CRDs/metrics API
and namespaces. The opt-in bootstrap verifies it before applying Flux. Shared
scaling-auth depends on the PostgreSQL, KEDA and RabbitMQ HelmReleases and requires the same pinned
controllers/CRDs/metrics
API and existing observer Secrets. The opt-in bootstrap verifies those
prerequisites before applying Flux; it does not provision their identities.
ADTOF also requires its existing worker Deployment, Ready ScaledObject, and
KEDA-owned HPA; bootstrap checks its native/Flux release through the worker
verifier. Its exact replica drift exception preserves KEDA scaling. Basic Pitch has
the same existing-worker prerequisites and a scoped replica exception, while
retaining both RabbitMQ and PostgreSQL task triggers. Demucs has the same
existing CPU worker/scaler/HPA requirements and dual authentication; the
bootstrap verifies it before applying Flux.
Keycloak bootstrap verifies delivery, its isolated database/login, administrator
Secret and durable realm/client/SMTP configuration without rerunning bootstrap.
MinIO bootstrap also verifies the existing storage release and its bucket/IAM/
notification configuration without changing them. Durable contents and
credentials remain outside Flux.
The node runtime must be able to pull `ghcr.io/fluxcd` controller images.
These four images are separate from the existing CloudDSP image mirror and
are pulled directly from GHCR for this opt-in installation.

The normal host `bootstrap-platform` command in
[`deploy-local.sh`](../scripts/deploy-local.sh) does not install Flux automatically.
Complete that deployment first using the [local deployment instructions](../../../README.md#local).
This opt-in script adopts existing healthy releases; it does not create the
k3d cluster or initialize an empty application installation.

For the upstream CloudDSP source, use a clean checkout of the published `main`
branch and inspect the URL and branch in
[`gotk-sync.yaml`](clusters/clouddsp-local/flux-system/gotk-sync.yaml).
Then run the bootstrap from the repository root:

```sh
git switch main
git pull --ff-only origin main
./k8Deployment/kubernetes/scripts/gitops/bootstrap-flux.sh
```

Use a separate checkout or worktree when the existing checkout has unrelated
local edits. The supplied script manages the dedicated `flux-system`
installation and sync configuration in `k3d-clouddsp-local`. Having the Flux CLI
installed does not mean the controllers or Git sync are installed. If controllers
already exist in this dedicated cluster, the script applies the reviewed pinned
installation and sync resources. A cluster with an existing Flux root for
another repository needs a reviewed integration before using this script.

The script rejects untracked or modified install manifests, checks prerequisites,
installs controller resources, waits for CRDs and controller availability, then
creates the Git source and reconciliation resource. It requests reconciliation
and reports Flux health. It does not publish Git automatically.

### Follow your own fork

Clone your public fork, complete the normal local deployment, then edit the
existing `GitRepository` in
[`gotk-sync.yaml`](clusters/clouddsp-local/flux-system/gotk-sync.yaml).
Change its `spec.url` and `spec.ref.branch`, preserving the rest of the manifest:

```yaml
spec:
  url: https://github.com/YOUR_USERNAME/CloudDSP.git
  ref:
    branch: main
```

Confirm that `origin` points to your fork, then commit and push the source
change before bootstrapping Flux:

```sh
git remote -v
git add k8Deployment/kubernetes/gitops/clusters/clouddsp-local/flux-system/gotk-sync.yaml
git commit -m "chore(gitops): track my CloudDSP fork"
git push origin main
./k8Deployment/kubernetes/scripts/gitops/bootstrap-flux.sh
```

This example uses `main`; substitute your branch in the manifest and Git
commands if you choose another branch. Later, commit and push chart or GitOps
changes to the same fork and branch. A pull request merged into upstream
CloudDSP updates clusters tracking upstream; it does not update a fork until
that change is merged into the fork's watched branch. Private repositories
require a dedicated read-only Git credential as described in the security
section above.

### Change an existing Git source

First commit and push the desired source URL and branch in the destination
repository's `gotk-sync.yaml`. When the current source is available, publish the
matching source change there too so Flux can observe the cutover. If the current
source is unavailable, such as after deleting its watched branch, update the
live GitRepository once so Flux can fetch the new declaration. For a switch to
CloudDSP's `main`:

```sh
kubectl --context k3d-clouddsp-local --namespace flux-system \
  patch gitrepository flux-system --type=merge \
  -p '{"spec":{"url":"https://github.com/Y1ktor/CloudDSP.git","ref":{"branch":"main"}}}'
flux reconcile source git flux-system \
  --context k3d-clouddsp-local --namespace flux-system
flux reconcile kustomization flux-system --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

The committed declaration must match the live patch. Otherwise the root
Kustomization can restore the earlier source settings on its next apply.
Reconciliation fetches and applies published configuration; it does not push
local commits or change the selected Git branch by itself.

## Inspect and reconcile

```sh
flux check --context k3d-clouddsp-local
flux get sources git --context k3d-clouddsp-local --namespace flux-system
flux get kustomizations --context k3d-clouddsp-local --namespace flux-system
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
kubectl --context k3d-clouddsp-local get pods --namespace flux-system
flux reconcile kustomization flux-system --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

With the upstream configuration, the Git source and root Kustomization should
show a `main@sha1:...` revision and `Ready=True`; each of the fifteen
HelmReleases should also be Ready. A cached artifact can still display an old
revision when a fresh fetch fails, so check readiness and the live source spec:

```sh
kubectl --context k3d-clouddsp-local --namespace flux-system \
  get gitrepository flux-system -o jsonpath='{.spec.url}{"\n"}{.spec.ref.branch}{"\n"}'
```

In Headlamp, inspect the `flux-system` namespace. The optional Headlamp Flux
plugin adds source, reconciliation, and HelmRelease views after installation.
Root readiness checks the Flux controller Deployments. Check each application's
HelmRelease readiness separately; root readiness does not establish
application readiness. Continue using `deploy-local.sh verify` and the current
component release helpers for application checks, including frontend health,
CSP, bundled assets, and SPA deep links.

## Regenerate the official installation

```sh
flux install --export --version=v2.9.6 \
  > k8Deployment/kubernetes/gitops/clusters/clouddsp-local/flux-system/gotk-components.yaml
```

For a Flux upgrade, generate the intended version, review the manifest changes,
validate the Kustomize build, and publish the commit to the configured branch
(`main` by default). Flux will reconcile the controller upgrade. Do not introduce image automation or
application HelmReleases without their separate ownership handoffs.

Official references: [installation](https://fluxcd.io/flux/installation/),
[GitRepository](https://fluxcd.io/flux/components/source/gitrepositories/), and
[Kustomization](https://fluxcd.io/flux/components/kustomize/kustomizations/).
