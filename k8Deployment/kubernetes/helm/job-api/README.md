# Job API Helm release

This chart owns the existing `Deployment/clouddsp-job-api`,
`Service/clouddsp-job-api`, and `Ingress/clouddsp-job-api` in `clouddsp-app`.
The Service keeps its internal ClusterIP and selects Pods only after the
PostgreSQL-aware `/readyz` probe passes. The Ingress keeps the `/auth` and
`/jobs` prefixes on the frontend's `clouddsp.localhost` origin; the frontend
release owns the separate `/` route. This chart creates no namespace, Secret,
database schema, bootstrap Job, image, or Keycloak configuration.

The templates mirror the three original manifests in
[`../../services/api/`](../../services/api/). Helm supplies release ownership
and namespace, plus the reviewed `images.job-api.immutableReference` from
[`../../images.lock.yaml`](../../images.lock.yaml) and the unchanged local
Ingress host. The Deployment selector, Pod labels, probes, resource/security
settings, runtime Secret names, Service ports, and Ingress paths remain
identical as Kubernetes objects. Credentials stay in pre-existing ignored
local Secrets, outside Helm values and release history.

## Adoption and verification

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/job-api-release.rb plan
./k8Deployment/kubernetes/scripts/job-api-release.rb adopt
./k8Deployment/kubernetes/scripts/job-api-release.rb verify
```

`plan` performs strict Helm lint, image-lock verification, render/source/live
spec comparison, and a Kubernetes server-side dry run. `adopt` repeats those
gates before an explicit one-time ownership transfer, then checks that all
three resource UIDs, the Service IP, and the API Pod UID stayed unchanged.
It does not automatically uninstall or roll back an adopted release.

`verify` compares Helm's stored manifest with the chart and live specs,
checks Pod readiness and its running image digest, and requests both browser
paths without an access token. `/auth/me` and `/jobs` must each return HTTP
401; a 200 would bypass authentication, while a 404 or 503 would show a
route or backend failure. The authenticated login, upload, and job lifecycle
smokes are separate end-to-end checks.

The versioned
[`../../tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml`](../../tests/keycloak-smoke/keycloak-job-api-auth-me-smoke-job.yaml)
passed on 2026-09-26 against this Helm-owned release. A temporary Keycloak
user's real token returned its exact subject from `/auth/me`, and `GET /jobs`
returned an empty list for that new owner. The test removed its temporary
Keycloak client/user, and the completed Job was deleted. This covers the
authenticated read path; upload and job lifecycle smokes remain separate.

On 2026-09-26, the existing objects were adopted as release
`clouddsp-job-api` revision 1. Their three resource UIDs, Service IP, Pod UID,
image digest, and protected same-origin routes were preserved.

Use this chart for reviewed Job API delivery changes. Keep the raw Deployment,
Service, and Ingress manifests in `services/api/` as the adoption comparison
baseline; do not reapply them to these Helm-owned objects. Database migration
and bootstrap manifests in that directory retain their separate one-time
process.
