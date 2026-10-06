# Frontend and Demucs refactor rollout — 2026-10-03

## Scope and result

Rebuilt and published the refactored shared frontend and local CPU Demucs
worker, synchronized the image lock, Helm values, and retained resource
baselines, then upgraded their existing releases in `k3d-clouddsp-local`.
The final root `deploy-local.sh verify` passed **all 56 configured gates**.
The live Demucs smoke passed and cleaned its reserved fixture. KEDA returned
Demucs and Basic Pitch to zero replicas after their normal warm cooldowns.

This was an update of the existing Mac ARM64 cluster. It was not a fresh VM
trial or an AWS frontend publication.

## Released images

| Component | Public Docker Hub tag | Manifest digest | Local uncompressed size |
| --- | --- | --- | --- |
| Frontend | `y1ktor/clouddsp:frontend-0.6.2-spa-route-fix` | `sha256:31ca2de7233c94611eda4f380240026476c2c3737c0bff9afd6fc6271dc33fab` | 35,137,059 bytes / 33.51 MiB |
| Demucs | `y1ktor/clouddsp:demucs-0.1.10-lease-refactor` | `sha256:6309797ec6911c5e4736efd1b8c6f1b10024e5d7021d679c1f82777db5e76fee` | 503,435,169 bytes / 480.11 MiB |

Both final images are also present in `clouddsp-registry.localhost:5001`.
Workloads use their immutable digest references. The publication runner checked
all 19 locked images against the local registry and public Docker Hub tags.
The locks therefore identify publicly available sources for future bootstrap
and image-mirror runs.

The frontend application source is commit `0c2499e`, including the earlier
MIDI editor split from `e151218`. Demucs worker source is commit `2334921`.
Version labels and the NGINX correction are part of this delivery update;
dependency and model locks remain the reviewed inputs recorded in the catalog.

## Build and source checks

- All 44 shared frontend tests and both cloud/local profile builds passed
  before image construction. Lint completed with three existing warnings.
  Mocked browser tests exercised authentication/session restoration, history,
  polling recovery, revision ordering, submission, and workspace behavior.
- The final frontend builder passed its local Vite build and `nginx -t`.
  Build wiring tests passed 5 tests / 29 assertions.
- Demucs passed 321 tests inside the image's Python 3.12 validation stage,
  FFprobe and ML import checks, verification of all four model artifacts,
  and actual two-second, two-stem ARM64 CPU inference from the worker's
  output-directory context.
- Both charts passed Helm lint and rendering. Kubernetes server dry runs
  accepted their resources before the upgrades.

## Routing issue found and corrected

The initial frontend `0.6.1-component-refactor` reached release revision 4.
Client-side navigation worked, but a direct `/architecture` request selected
the existing public illustration directory, redirected to `/architecture/`,
and returned HTTP 403 because that directory had no index.

NGINX now uses `try_files $uri /index.html`, checking real asset files before
falling back to the SPA document. The former `$uri/` directory candidate
caused the collision; its directory behavior is described in the
[NGINX directive documentation](https://nginx.org/en/docs/http/ngx_http_core_module.html#try_files).
The corrected final image has a separate `0.6.2-spa-route-fix` tag and digest.

A disposable non-root, read-only container proved direct HTTP 200 responses,
identical SPA bodies, and no redirects for `/architecture`, `/architecture/`,
`/k8`, and `/cost`. The architecture PNG still returned `image/png` with its
original SHA-256. The container was removed.

Those four direct routes are now part of the versioned frontend verification
gate, including equal CSP headers. Seven regression tests / 55 assertions
cover success, redirects, 403, incorrect shell/policy, optional route checks,
and preserved protected API checks. Existing Helm, Job API, Mailpit, and
Keycloak helper tests passed 12 tests / 79 assertions.

The live browser confirmed Architecture direct navigation and refresh, K8
content and its security panel, the Cost route, and the local Keycloak sign-in
redirect using S256 PKCE. This trial did not repeat authenticated browser
upload, saved-job reopening, or MIDI playback; the earlier consolidation
trial and this refactor's mocked browser checks cover those frontend workflows.
The optional public demo catalog is not published in this local deployment;
the browser falls back to an empty catalog and logs a non-fatal warning.
This does not prevent Keycloak sign-in or authenticated processing.

## Helm and live worker evidence

| Release | Final chart | Final Helm revision | Result |
| --- | --- | --- | --- |
| `clouddsp-frontend` | `frontend-0.1.3` | 5 | Deployed, one ready replica, direct routes/CSP/assets verified |
| `clouddsp-demucs` | `demucs-0.1.1` | 3 | Deployed, new digest used by a real worker, idle zero verified |

These were ordinary `helm upgrade` operations. Deployment and frontend Service
UIDs were preserved, as were the Demucs ScaledObject and all generated worker
HPA UIDs. Demucs replicas remain omitted from the chart and controlled by KEDA.
The live Demucs Pod's configured image and runtime image ID both matched the
new digest during processing.

The [versioned Demucs smoke](../../tests/demucs-worker-smoke/README.md) used
its existing ignored credential sources to provision restricted test identities.
The two temporary bootstrap Secret copies were deleted after bootstrap success;
the completed bootstrap Jobs expired through their 600-second TTL. The runtime
test identities remain restricted to the reserved fixture for later smoke runs.

The smoke proved:

- Source upload and a durable source event passed through the real dispatcher.
- Demucs completed on its first attempt and cleared its lease.
- Both private stem objects had the expected metadata, byte counts, and
  independently checked SHA-256 values.
- Two downstream Basic Pitch outbox events were committed with the result.
- Both downstream tasks and the parent Job completed before cleanup.
- The client removed only its five reserved object keys and guarded database
  fixture. The release runner removed the successful smoke Job.

After the normal cooldown, Demucs and Basic Pitch had zero desired replicas,
and no Demucs Pod remained. Final root verification passed all 56 gates,
including both updated releases and the new direct-route checks.

## Commands used for delivery

From the repository root, with the local registry and cluster already running:

```bash
./k8Deployment/kubernetes/scripts/images/build-frontend-image.sh
./k8Deployment/kubernetes/scripts/images/build-demucs-image.sh
ruby k8Deployment/kubernetes/scripts/images/image-registry-stage.rb publish

helm upgrade clouddsp-frontend k8Deployment/kubernetes/helm/frontend \
  --kube-context k3d-clouddsp-local --namespace clouddsp-app --wait --timeout 3m
helm upgrade clouddsp-demucs k8Deployment/kubernetes/helm/demucs \
  --kube-context k3d-clouddsp-local --namespace clouddsp-app --wait --timeout 3m

# After provisioning the restricted identities in the smoke runbook:
ruby k8Deployment/kubernetes/scripts/releases/demucs-release.rb smoke
./k8Deployment/kubernetes/scripts/deploy-local.sh verify
```

The frontend build/publication/upgrade were repeated for the routing correction.
The final image lock and chart values contain the recorded final digests.
