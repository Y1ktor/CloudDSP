# Frontend's handoff from direct Helm to Flux

The versioned Flux root selects the existing `clouddsp-frontend` native Helm
release. Its HelmRelease API object lives in `flux-system`; the release,
revision Secrets, Deployment, Service, and Ingress stay in `clouddsp-app`.
The chart remains version `0.1.3` with the reviewed image and values unchanged.
This guide describes the source-defined handoff and checks; it does not record
a completed live adoption or a passing browser trial.

## Desired configuration and ownership

- [HelmRelease](clusters/clouddsp-local/frontend/helmrelease.yaml) fixes
  `releaseName`, `targetNamespace`, and `storageNamespace` to the existing
  release/storage layout. Keep these identifiers stable: changing them can
  uninstall the previous release and lose continuity with its Helm history.
- [Reconciliation RBAC](clusters/clouddsp-local/frontend/reconciliation-rbac.yaml)
  provides `clouddsp-frontend-helm` in `flux-system`, impersonated during Helm
  actions through a RoleBinding in `clouddsp-app`. Its Role permits changes to
  Services, Deployments, and Ingresses, with read-only Pods and ReplicaSets for
  readiness. Helm requires namespace-wide Secret access to list and write its
  revision storage; this grant does not restrict access to only frontend Secret
  names. It grants no PVCs, Jobs, CRDs, namespace creation, or cluster RBAC.
  Trusted Git writers can still change the HelmRelease and bootstrap RBAC.
- The [existing chart](../helm/frontend/) stays canonical. The Git source
  includes GitOps configuration and the explicitly selected Mailpit/frontend/Job API/upload-intake/generic-dispatcher
  charts. Frontend owns no Keycloak or Job API resource, Secret, or image build.
- `reconcileStrategy: Revision` rebuilds the Git chart after source changes.
  Flux packages a version such as `0.1.3+abcdef123456.1`; the base chart remains
  `0.1.3`, and the suffix identifies the source revision and HelmChart generation.
  `valuesFiles` uses the repository-root frontend values path rather than a
  path relative to the chart directory.
- Flux adds `helm.toolkit.fluxcd.io/name` and `/namespace` to resource-level
  labels. The Deployment Pod template, Helm ownership annotations, and
  `managed-by: Helm` keep their existing meanings. The helper checks exact
  label values when verifying the Flux-owned layout.

Flux finds the deployed release in its existing Helm storage and adopts it
through an upgrade. There is no adopt-only HelmRelease mode: if the release is
absent, this configuration can install it. Verify the existing release,
source values, ready image, and browser route before enabling this root on
another cluster. Ordinary fresh frontend installation retains the existing
Keycloak configuration and Job API verification prerequisites in the release
helper. These services remain outside Flux ownership, so the frontend
HelmRelease does not declare `dependsOn` entries for them.

The ordinary 93-stage `bootstrap-platform` command still creates the cluster,
installs application releases, and provisions service state before the separate
opt-in Flux bootstrap. It does not install Flux automatically.

## Verify the release and browser delivery

Run the current release helper from the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-frontend --namespace clouddsp-app
./k8Deployment/kubernetes/scripts/releases/frontend-release.rb verify
```

The [release helper](../scripts/releases/frontend-release.rb) uses the current
[shared Helm helper](../scripts/lib/helm-release.rb) and
[frontend Flux ownership adapter](../scripts/gitops/frontend-flux-ownership.rb).
Verification checks the expected HelmRelease identity, readiness, exact Flux
resource labels, and Git chart revision. It retains source/render/stored/live
spec equality, locked image, Helm ownership, and Pod readiness checks.

Browser verification requests `/healthz`, the app shell, the Content Security
Policy, and the linked JavaScript and CSS assets through Traefik at
`clouddsp.localhost:8080`. Direct `/architecture`, `/architecture/`, `/k8`, and
`/cost` requests must return the same shell and CSP without a redirect. These
checks do not establish authenticated browser flows or processing success.
There is no chart Helm test hook, so HelmRelease tests remain disabled.

The helper refuses direct `install` or `adopt` whenever the matching Flux
HelmRelease exists, including while suspended, unhealthy, or deleting. This
protects repository commands; cluster administrators can still run standalone
Helm commands. Ordinary delivery changes now belong in Git after handoff.

## Change or recover the release

Edit the chart or values, run `helm lint` and `helm template`, and commit and
push the reviewed change to `codex/flux-clouddsp-local`. Then optionally request
immediate reconciliation:

```sh
flux reconcile helmrelease clouddsp-frontend --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Drift detection is enabled: after the release reaches its desired Helm state,
Flux corrects direct live changes against the stored rendered manifest. Keep
versioned values and the locked image consistent with the retained source
manifest so the release helper can continue to compare the full boundary.

The handoff preserves Helm's automatic apply-method selection. Taking over
unrelated resources, automatic rollback/uninstall remediation, forced
replacement, and cleanup of new resources after a failed upgrade are disabled.
A failed action can still leave partial changes; inspect HelmRelease conditions
and native Helm history, then repair the desired configuration in Git. A new
chart revision starts a new attempt. To retry the same reviewed configuration
after a transient failure, inspect it first and use:

```sh
flux reconcile helmrelease clouddsp-frontend --reset \
  --context k3d-clouddsp-local --namespace flux-system
```

Removing an active HelmRelease from Git normally uninstalls its workload
objects. Design an explicit suspended-release/orphan procedure before handing
ownership back to another tool; deleting the HelmRelease is a lifecycle action.
The retained raw `services/frontend/` manifests remain comparison inputs and
must not be reapplied to bypass the release owner.

Official references: [HelmRelease](https://fluxcd.io/flux/components/helm/helmreleases/),
[Git chart packaging](https://fluxcd.io/flux/components/source/helmcharts/), and
[drift detection](https://fluxcd.io/flux/components/helm/helmreleases/#drift-detection).
