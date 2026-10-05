# Frontend Helm release

This chart owns the local `Deployment/clouddsp-frontend`,
`Service/clouddsp-frontend`, and `Ingress/clouddsp-frontend` in
`clouddsp-app`. The Service retains its ClusterIP, selector, and port 80; the
Ingress retains `clouddsp.localhost`, the exact local Keycloak redirect and
logout origin. The chart creates no namespace, Secret, ConfigMap, or image.

The templates mirror the legacy
[`../../services/frontend/`](../../services/frontend/) manifests. During
adoption, the rendered specs must match those manifests and the live objects.
Helm supplies only release ownership metadata, the release namespace, the
reviewed image digest from [`../../images.lock.yaml`](../../images.lock.yaml),
and the unchanged local host value. The Pod template and immutable Deployment
selector remain identical as Kubernetes objects. Public Vite
configuration and the Content Security Policy are baked into the existing
static image; no browser credential is placed in Helm values.

## One-time adoption and checks

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/frontend-release.rb plan
./k8Deployment/kubernetes/scripts/releases/frontend-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/frontend-release.rb verify
```

`plan` runs Helm lint/template, checks the image lock, validates the render
through a Kubernetes server-side dry run, and compares all declared fields
with the three original source and live objects. `adopt` repeats the checks,
uses Helm's explicit ownership transfer, and verifies the three original
resource UIDs, Service IP, and Pod UID are unchanged. The shared
[`helm-release.rb`](../../scripts/lib/helm-release.rb) helper uses
`--force-conflicts` only after spec equality has passed. It
never uses automatic uninstall/rollback during an adoption; deleting an
adopted release could delete these original objects.

`verify` checks that Helm's stored release manifest still equals the chart,
all three live specs and ownership markers match, the Pod is Ready, and
Traefik serves `/healthz`, the HTML app shell, its Content Security Policy,
and the linked JavaScript and CSS assets. This is a deployment-path smoke
check; it does not replace an authenticated browser or end-to-end processing
test.

On 2026-09-26, the local adoption completed as release
`clouddsp-frontend` revision 1. The three resource UIDs, Service IP, and Pod
UID were unchanged. The `clouddsp.localhost:8080` health route, app shell,
Content Security Policy, and bundled assets passed verification. Mailpit and
all other component release boundaries remain separate.

After adoption, use this chart and a separately reviewed normal Helm upgrade
for frontend delivery changes. The raw `services/frontend/` manifests remain
as the adoption baseline and must not be reapplied to Helm-owned objects.
