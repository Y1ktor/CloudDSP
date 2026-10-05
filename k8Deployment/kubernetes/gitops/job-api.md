# Job API's handoff from direct Helm to Flux

The versioned root selects the existing native `clouddsp-job-api` Helm release.
Its HelmRelease is in `flux-system`; its native release, revision Secrets,
Deployment, Service, and Ingress stay in `clouddsp-app`. The base chart remains
`0.1.0`, with the locked image and values unchanged. Check current-generation
readiness and the release verifier to establish the state of a running cluster.

## Ownership and prerequisites

- The [HelmRelease](clusters/clouddsp-local/job-api/helmrelease.yaml) fixes the
  existing `releaseName`, `targetNamespace`, and `storageNamespace`. Changing
  these identifiers can uninstall the previous release or lose Helm history.
- [Reconciliation RBAC](clusters/clouddsp-local/job-api/reconciliation-rbac.yaml)
  gives `flux-system/clouddsp-job-api-helm` chart-kind permissions only within
  `clouddsp-app`. Services, Deployments, and Ingresses can be changed; Pods and
  ReplicaSets are read-only. Helm storage requires namespace-wide Secret
  access. This grant is not limited to this release's Secret names. No Jobs,
  PVCs, CRDs, namespace creation, or cluster RBAC are granted. The root Flux
  controller and trusted Git writers retain their existing administrative scope.
- The [chart](../helm/job-api/) owns three objects only. Runtime credentials,
  PostgreSQL roles/schema/migrations, MinIO IAM and buckets, and Keycloak realm
  settings retain their separate bootstrap stages. Secret references remain in
  the Pod spec; credential values are excluded from Git and chart values.
- Before enabling reconciliation, verify the deployed native release and its
  dependencies through the existing bootstrap gates. Job API now depends on the
  [Keycloak](keycloak.md), [PostgreSQL](postgresql.md) and [MinIO](minio.md)
  HelmReleases; readiness does not provision schemas, IAM or buckets.
  Keycloak realm/client settings keep their bootstrap ownership. A missing
  release can be installed by this configuration; there is no adopt-only mode.
- The source artifact must contain `helm/job-api/` before adding its
  HelmRelease to the root. Source-path preparation is published and verified
  first, then ownership is enabled in a second commit. This avoids a chart
  build racing an expansion of the GitRepository's sparse archive.

Flux upgrades the native release in its original storage namespace. Git revision
packaging adds a SHA/generation suffix such as `0.1.0+abcdef123456.1`, while the
chart's base version stays unchanged. Flux adds its exact name/namespace labels
to resource-level metadata, leaving the Pod template, selectors, Secret
references, ports, and same-origin `/auth` and `/jobs` ingress paths unchanged.
The frontend's `/` route remains a separate release.

## Verification and authenticated smoke

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-job-api --namespace clouddsp-app
./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify
```

The [release runner](../scripts/releases/job-api-release.rb) and
[Flux adapter](../scripts/gitops/job-api-flux-ownership.rb) retain exact
source/render/stored/live parity, native Helm ownership, current-generation
Flux readiness, the running locked image digest, and Ready Pod checks. Requests
without a token to `/auth/me` and `/jobs` through Traefik must both return
HTTP 401. A 200 would bypass authentication; a 404/503 would indicate a routing
or backend failure.

The separate [authenticated read smoke](../tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml)
creates a uniquely named temporary Keycloak client/user, obtains a real token,
and calls the internal API Service. `/auth/me` must return that exact subject
and `/jobs` an empty list for the new owner. It deletes its test identities,
never prints credentials, and creates no processing job. The permanent React
client keeps its Authorization Code + PKCE configuration.

Run only when the named smoke Job is absent. Inspect an existing Job before
rerunning; do not automatically replace an active test. After a successful
completion, inspect its safe output and remove the disposable Job:

```sh
kubectl --context k3d-clouddsp-local create \
  -f k8Deployment/kubernetes/tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml
kubectl --context k3d-clouddsp-local -n clouddsp-data wait \
  job/keycloak-job-api-auth-me-smoke --for=condition=complete --timeout=180s
kubectl --context k3d-clouddsp-local -n clouddsp-data logs job/keycloak-job-api-auth-me-smoke
kubectl --context k3d-clouddsp-local -n clouddsp-data delete \
  job/keycloak-job-api-auth-me-smoke --wait=true
```

A failed/interrupted test needs diagnosis, including possible leftover test
identities. This smoke is separate from authenticated browser login, uploads,
and processing end-to-end tests; it is not a Helm test hook. Flux tests are
disabled and the reconciliation identity cannot create this smoke Job.

## Delivery and recovery

Commit chart/values changes to `codex/flux-clouddsp-local`, keeping the image
lock and retained source manifests consistent. Drift detection repairs direct
live changes against the stored desired manifest. The runner blocks direct
`install` and `adopt` whenever its HelmRelease exists, including suspended,
failed, and deleting states. API lookup failures do not grant write permission.

To request reconciliation after inspecting the source and release conditions:

```sh
flux reconcile helmrelease clouddsp-job-api --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Automatic rollback/uninstall remediation, forced replacement, and failed-upgrade
cleanup are disabled. A failed action can still leave partial changes; inspect
the HelmRelease and native history, then repair Git. A new chart revision starts
a new attempt; a diagnosed transient failure can be retried with
`flux reconcile helmrelease clouddsp-job-api --reset` using the same context and
namespace. Deleting an active HelmRelease normally uninstalls its objects.
Design an explicit ownership return procedure before handing it to another tool.

The ordinary fresh bootstrap still provisions dependencies and directly
installs the release before opt-in Flux bootstrap. Host cluster/registry
lifecycle and database/service state are outside this handoff.
