# Mailpit Flux handoff trial — 2026-10-04

## Source and boundary

- Cluster context: `k3d-clouddsp-local`, Kubernetes `1.35.5+k3s1`.
- Flux: `v2.9.6`, with helm-controller `v1.6.5`.
- GitOps branch: `codex/flux-clouddsp-local`.
- Applied source commit: `6d0e46439527791766f2b23238ad64831afb65f1`.
- Native release: `clouddsp-mailpit`, target and storage namespace `clouddsp-data`.
- HelmRelease: `flux-system/clouddsp-mailpit`.
- Generated Git chart version: `0.1.0+6d0e46439527.1`.

The handoff reused native Helm storage and upgraded release revision 1 to
revision 2. Revision 1 remains in Helm history as superseded; revision 2 is
deployed. No uninstall or reinstall was performed. The chart values and
Mailpit resource specifications were unchanged. Flux added only its expected
resource-level ownership labels.

## Checks performed

1. Verified the manual Helm release and Mailpit browser readiness before publishing.
2. Passed strict Helm lint, Kustomize rendering, Kubernetes server dry runs,
   and whitespace checks for the desired HelmRelease, RBAC, and Git source.
3. Passed adapter tests against both modern and legacy helpers: 10 tests and
   210 assertions in each layout. Existing Mailpit fresh-install tests also passed.
4. Published the versioned handoff, reconciled the root Git source, then
   reconciled the Mailpit HelmRelease and generated HelmChart.
5. Both current-workspace and GitOps-checkout Mailpit verifiers passed against
   the Ready Flux release, stored native manifest, live specs, Pod, and HTTP route.
6. Compared pre/post object JSON: all four resource UIDs and full specs match,
   including both Service IPs. The existing Mailpit Pod UID also matches.
7. Ran the versioned SMTP capture smoke Job. It sent one harmless local test
   message and found the captured message through the ClusterIP HTTP Service.
   The smoke Job and its Pod were removed after success.

## Preserved identities

| Object | Preserved UID |
| --- | --- |
| `Deployment/clouddsp-mailpit` | `c34eba7c-8fce-4af6-a953-966e0541d7b4` |
| `Service/clouddsp-mailpit-smtp` | `a977b705-b6de-4002-9fe0-0aae60e8c5b8` |
| `Service/clouddsp-mailpit` | `0a399d3b-dd30-4674-a863-883b0a3fcb40` |
| `Ingress/clouddsp-mailpit` | `95546933-2afd-4faa-87cc-e6bca5fe368f` |

The preserved Mailpit Pod UID was `a64a5fa3-419a-4016-bc62-b891766b0c33`.

## Operating consequences

Ordinary Mailpit changes now go to the watched GitOps branch. Its helper
blocks direct install/adopt while the matching HelmRelease exists, including
suspended, unhealthy, or deleting states. Verify/smoke require Ready status
for the current generation and retain exact manifest/spec/ownership checks.
Drift correction is configured as enabled; this trial did not inject drift.

The reconciliation Role is scoped to `clouddsp-data`, including Secret access
required by the existing Helm release history. No populated credentials were
committed or copied into GitOps. Other application releases retain their
existing owners. This trial does not establish full application health;
frontend and dispatcher readiness issues were already observed before Flux
bootstrap and were outside this Mailpit handoff.
