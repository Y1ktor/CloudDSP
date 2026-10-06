# Frontend Helm release

This chart owns the local `Deployment/clouddsp-frontend`,
`Service/clouddsp-frontend`, and `Ingress/clouddsp-frontend` in
`clouddsp-app`. The Service retains its ClusterIP, selector, and port 80; the
Ingress retains `clouddsp.localhost`, the exact local Keycloak redirect and
logout origin. The chart creates no namespace, Secret, ConfigMap, or image.

The templates mirror the retained
[`../../services/frontend/`](../../services/frontend/) manifests. The shared
release helper compares the rendered specs with those manifests and the live
objects. Helm supplies release ownership metadata, the release namespace,
the reviewed image digest from [`../../images.lock.yaml`](../../images.lock.yaml),
and the local host value. Public Vite configuration and the Content Security
Policy are baked into the static image; browser credentials do not belong in
Helm values.

## Delivery owner and verification

The [frontend Flux handoff](../../gitops/frontend.md) selects the existing
native `clouddsp-frontend` release and its Helm storage in `clouddsp-app`.
The HelmRelease and its impersonated service account live in `flux-system`;
a namespace Role limits chart actions to the existing application namespace.
The base chart remains `0.1.3`; Git revision packaging supplies Flux's revision
suffix. Its image digest, values, Deployment Pod template, immutable selector,
and browser origin are unchanged by this ownership configuration.

From the repository root:

```sh
./k8Deployment/kubernetes/scripts/releases/frontend-release.rb verify
```

The current [release helper](../../scripts/releases/frontend-release.rb),
[shared Helm helper](../../scripts/lib/helm-release.rb), and
[Flux ownership adapter](../../scripts/gitops/frontend-flux-ownership.rb) check
both direct Helm and Flux-owned layouts. They verify source/render/stored/live
spec equality, Helm ownership, the locked image, Pod readiness, and exact Flux
labels/chart revision when the matching HelmRelease is present.

The browser check requests `/healthz`, the HTML app shell, its Content Security
Policy, and linked JavaScript/CSS through Traefik. It also requires direct
`/architecture`, `/architecture/`, `/k8`, and `/cost` navigation to return the
same app shell and CSP without redirects. This deployment-path check does not
replace authenticated browser or processing tests. The chart has no Helm test
hook; Flux tests stay disabled.

After the handoff, delivery changes go through the versioned chart and values
on `codex/flux-clouddsp-local`. The helper blocks direct `install` and `adopt`
while the matching Flux HelmRelease exists, including while it is suspended,
unhealthy, or deleting. Read the [handoff recovery rules](../../gitops/frontend.md#change-or-recover-the-release)
before changing release identity, deleting its HelmRelease, or attempting a
manual recovery.

## Initial installation and adoption history

Without the matching Flux HelmRelease, the helper retains `plan`, `adopt`,
and the fresh `install` path. `plan` runs Helm lint/template, image-lock and
server-side dry-run checks, and compares all declared fields with the retained
source and live objects. `adopt` transfers ownership only after equality checks
and verifies resource UIDs, Service IP, and Pod UID are unchanged. Fresh
installation verifies Keycloak configuration and the Job API first. The root
`bootstrap-platform` sequence still uses these existing prerequisites and does
not install Flux automatically.

On 2026-09-26, the original direct Helm adoption completed as release
`clouddsp-frontend` revision 1 with the three resource UIDs, Service IP, and Pod
UID unchanged. That recorded trial passed health, shell, CSP, and bundled asset
checks. The current Flux configuration does not itself establish a successful
live frontend handoff; use the verification commands above to inspect the
running cluster.

The raw `services/frontend/` manifests remain comparison inputs and must not
be reapplied to Helm-owned objects. Keycloak, Job API, Mailpit, and all other
component release boundaries remain separate.
