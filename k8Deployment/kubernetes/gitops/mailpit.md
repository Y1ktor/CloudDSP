# Mailpit's handoff from direct Helm to Flux

Flux manages the existing `clouddsp-mailpit` native Helm release. Its
HelmRelease API object lives in `flux-system`; the release, revision Secrets,
and four Mailpit resources stay in `clouddsp-data`. No namespace, Secret
credentials, storage volume, SMTP exposure, or image change is part of adoption.

## Desired configuration and ownership

- [HelmRelease](clusters/clouddsp-local/mailpit/helmrelease.yaml) selects the
  existing release and storage namespace explicitly. Keep those identifiers
  stable: changing them can uninstall the previous release.
- [Reconciliation RBAC](clusters/clouddsp-local/mailpit/reconciliation-rbac.yaml)
  supplies a service account impersonated for Helm operations. Its Role covers
  only the data namespace and the Mailpit chart's API kinds. Helm needs access
  to namespace Secrets for its existing revision storage; that permission is
  broader than only Mailpit Secret names. Trusted Git writers can still change
  the HelmRelease or bootstrap RBAC; this is a scoped chart action identity,
  not a complete multi-tenant security boundary.
- The [existing chart](../helm/mailpit/) remains canonical. Its values preserve
  the locked image and `mailpit.localhost` route. The Git source includes
  GitOps configuration and the explicitly selected Mailpit/frontend charts.
- `reconcileStrategy: Revision` rebuilds the chart after source changes. Flux
  creates a chart version such as `0.1.0+abcdef123456.1`; the base chart remains
  `0.1.0`, and the suffix records Git revision and HelmChart generation.
- Flux adds `helm.toolkit.fluxcd.io/name` and `/namespace` to resource-level
  labels. It does not add them to the Deployment Pod template. Helm's release
  annotations and `managed-by: Helm` remain unchanged.

Flux detects the deployed native release in its existing Helm storage and
adopts it through an upgrade. There is no adopt-only HelmRelease mode: if the
release is absent, this configuration can install it. Check the current
release and exact chart values before enabling the handoff on another cluster.

## Verify and run the SMTP smoke test

Run the current release helper from the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-mailpit --namespace clouddsp-data
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb verify
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb smoke
```

The [release helper](../scripts/releases/mailpit-release.rb) uses the current
[shared Helm helper](../scripts/lib/helm-release.rb) and
[Flux ownership adapter](../scripts/gitops/mailpit-flux-ownership.rb). The adapter
validates the expected HelmRelease, current readiness, exact Flux label values,
and the Git chart revision while preserving source/render/stored/live spec,
image, and Pod readiness checks.

The helper refuses direct `install` or `adopt` whenever the matching Flux
HelmRelease exists, including while suspended, unhealthy, or deleting. This
protects repository commands; a cluster administrator can still run standalone
Helm commands. Ordinary changes now belong in Git.

The smoke Job sends one harmless internal test email and checks the captured
message through Mailpit's HTTP Service. Its Job is deleted after success;
Mailpit retains the captured message in its disposable inbox.

## Change or recover the release

Edit the chart or values, run `helm lint` and `helm template`, and commit and
push the reviewed change to `codex/flux-clouddsp-local`. Then optionally request
immediate reconciliation:

```sh
flux reconcile helmrelease clouddsp-mailpit --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Drift detection is enabled: Flux corrects direct live changes using the stored
rendered release manifest after adoption reaches the desired Helm state.
Keep the versioned chart, values, and locked image configuration consistent
so CloudDSP verification can confirm the result.

Automatic rollback, uninstall remediation, forced replacement, and cleanup of
new resources after an upgrade failure are disabled. A failed action can still
leave partial changes; inspect the HelmRelease conditions and native Helm
history, then repair the configuration in Git. Changing the desired chart
revision starts a new attempt. To retry the same reviewed configuration after
a transient failure, inspect it first and use:

```sh
flux reconcile helmrelease clouddsp-mailpit --reset \
  --context k3d-clouddsp-local --namespace flux-system
```

Removing this active HelmRelease from Git normally uninstalls Mailpit and
loses its disposable inbox when the Pod is deleted. To hand ownership back to
another tool, design an explicit suspended-release/orphan procedure first;
do not delete the HelmRelease as an ownership-only action.

Official references: [HelmRelease](https://fluxcd.io/flux/components/helm/helmreleases/),
[Git chart packaging](https://fluxcd.io/flux/components/source/helmcharts/), and
[drift detection](https://fluxcd.io/flux/components/helm/helmreleases/#drift-detection).
