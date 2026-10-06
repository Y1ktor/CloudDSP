# Mailpit Flux label round-trip trial — 2026-10-04

## Result

Passed on the running Mac context `k3d-clouddsp-local`. A pushed Git change
added `MP_LABEL="CloudDSP - Flux test"`; Flux automatically performed the
Helm upgrade and replaced the Mailpit Pod. A pushed Git revert then restored
the original configuration through another automatic Helm upgrade. Ownership,
readiness, browser ingress, and SMTP capture checks passed in both phases.

No manual `flux reconcile`, `helm upgrade`, `helm rollback`, or live Deployment
patch was used. GitRepository polling, chart packaging, and helm-controller
reconciliation performed both updates.

## Git and Helm revisions

The source remains the `codex/flux-clouddsp-local` branch of
`https://github.com/Y1ktor/CloudDSP.git`. Native Helm storage and workload
namespace remain `clouddsp-data`; the HelmRelease is in `flux-system`.

| Phase | Git commit | Native Helm revision | Chart version | Browser API label |
| --- | --- | --- | --- | --- |
| Before | `6d0e46439527791766f2b23238ad64831afb65f1` | 2 | `0.1.0+6d0e46439527.1` | Empty |
| Label | `69c889ffe07b9cf135574c4b897dd9a8feb5329f` | 3 | `0.1.0+69c889ffe07b.1` | `CloudDSP - Flux test` |
| Restored | `527cc8f3516853408e9921a32fead9f8030e0525` | 4 | `0.1.0+527cc8f35168.1` | Empty |

The label commit changed only the Mailpit Deployment's environment in the
Helm template and its retained raw verification baseline. The revert restores
both files exactly. It is a new desired Git revision and Helm upgrade; it
does not return the release-history counter to revision 2.

## Observed timing

Times are UTC. Push timestamps were recorded immediately after successful
pushes; completion uses the HelmRelease Ready condition transition time.
They describe this run rather than a reconciliation-time guarantee.

| Phase | Push recorded | Source artifact updated | HelmRelease Ready | Approximate push-to-ready |
| --- | --- | --- | --- | --- |
| Label | 21:53:01 | 21:53:41 | 21:53:53 | 52 seconds |
| Restored | 21:55:16 | 21:55:51 | 21:56:02 | 46 seconds |

The Git source polls every minute. Matching source commit and chart revision
were checked along with readiness, so a Ready condition from the previous
revision could not satisfy the test. GitRepository, Flux HelmChart,
HelmRelease, and root Kustomization were all Ready at final inspection.

## Resource and Pod comparisons

Snapshots before, during, and after the change proved:

- All four resource UIDs remained unchanged.
- The label phase changed only `MP_LABEL` in the Deployment spec.
- All restored resource specs exactly matched their original live specs.
- Both complete Service specs, including ClusterIPs, stayed unchanged.
- The Ingress spec and load-balancer address stayed unchanged.
- Each phase had one Ready Mailpit Pod, with three distinct Pod UIDs.
- The configured image and running container image ID stayed unchanged.

| Resource | Preserved UID | Preserved ClusterIP |
| --- | --- | --- |
| Deployment/clouddsp-mailpit | `c34eba7c-8fce-4af6-a953-966e0541d7b4` | — |
| Service/clouddsp-mailpit-smtp | `a977b705-b6de-4002-9fe0-0aae60e8c5b8` | `10.43.132.118` |
| Service/clouddsp-mailpit | `0a399d3b-dd30-4674-a863-883b0a3fcb40` | `10.43.149.5` |
| Ingress/clouddsp-mailpit | `95546933-2afd-4faa-87cc-e6bca5fe368f` | — |

| Phase | Pod | UID |
| --- | --- | --- |
| Before | `clouddsp-mailpit-6d5cd89c8-r8674` | `a64a5fa3-419a-4016-bc62-b891766b0c33` |
| Label | `clouddsp-mailpit-7f55fb5f6c-jn5xh` | `2e34d171-0686-4ddd-993c-63ba3719d767` |
| Restored | `clouddsp-mailpit-6d5cd89c8-7dhm2` | `039fe2c7-b84c-4857-a71d-1807715ada85` |

Mailpit uses `Recreate` and Pod-local `emptyDir` storage. Both changes briefly
interrupted its endpoints and cleared the preceding inbox. The final SMTP
smoke test added a fresh captured test message. This trial changes no
image, Service port, security setting, persistent volume, or other application.

## Validation

- Mailpit Helm strict lint passed.
- Server-side chart dry run passed against the running cluster schemas.
- Flux ownership adapter regression suite passed: 10 tests, 210 assertions.
- Git whitespace checks passed before the test commit.
- Existing helper verification passed in both phases: strict source/chart/
  installed/live parity, Helm ownership, Pod readiness, and ingress health.
- `http://mailpit.localhost:8080/api/v1/webui` returned the expected `Label`
  value in both phases. Mailpit v1.30.6 renders this value above Inbox in the
  sidebar and appends it to the browser tab title; the user was prompted to
  refresh while the label phase was live.
- Existing SMTP capture smoke passed in both phases, sending through the
  internal SMTP Service and checking the captured message through the HTTP
  Service. The helper removed each successful disposable smoke Job.
- The GitOps worktree is clean and its final Mailpit source equals the
  pre-test source. The primary workspace's temporary label edits were also
  removed, preserving its preceding unrelated work.

Current primary-workspace checks:

```sh
ruby k8Deployment/kubernetes/scripts/releases/mailpit-release.rb verify
ruby k8Deployment/kubernetes/scripts/releases/mailpit-release.rb smoke
```

The watched branch uses the older helper location
`k8Deployment/kubernetes/scripts/mailpit-release.rb`. During the label phase,
its checkout and the primary checkout were kept in sync for strict verification.

Read-only release inspection:

```sh
kubectl --context k3d-clouddsp-local -n flux-system get helmreleases.helm.toolkit.fluxcd.io clouddsp-mailpit
kubectl --context k3d-clouddsp-local -n flux-system get helmcharts.source.toolkit.fluxcd.io flux-system-clouddsp-mailpit
helm --kube-context k3d-clouddsp-local -n clouddsp-data history clouddsp-mailpit
```

Use the fully qualified Flux HelmChart resource name: K3s also exposes a
different `HelmChart` kind under `helm.cattle.io`.
