# Flux bootstrap for the local CloudDSP cluster

This opt-in bootstrap installs **Flux v2.9.6** in `flux-system` and connects
it to the public CloudDSP GitHub repository. It manages its own installation
and the explicitly adopted Mailpit Helm release. Other application releases
and their bootstrap identities retain their existing script ownership. See
the [Mailpit handoff guide](mailpit.md) for its source, verification, and
recovery rules.

## Git and cluster boundaries

| Setting | Current configuration |
| --- | --- |
| Kubernetes context | `k3d-clouddsp-local` |
| Git repository | `https://github.com/Y1ktor/CloudDSP.git` |
| GitOps branch | `codex/flux-clouddsp-local` |
| Reconciled directory | `k8Deployment/kubernetes/gitops/clusters/clouddsp-local` |
| Git poll and reconciliation interval | One minute |

The dedicated branch carries the reviewed source alignment for the canonical
[shared frontend](../../../frontend/), current [Helm charts](../helm/),
[image lock](../images.lock.yaml), and organized [deployment helpers](../scripts/).
This source alignment prepares later component adoptions; live reconciliation
remains limited to Flux and Mailpit. Changes made only on another branch are not
deployed. A later move to `main` must publish these files there and update
`spec.ref.branch` in `flux-system/gotk-sync.yaml` as one coordinated change.

`GitRepository` is a Flux API resource: source-controller downloads its branch.
The Flux `Kustomization` API resource selects the directory to apply. The
`kustomization.yaml` files are Kustomize build inputs, with explicit resource
lists so retained raw service manifests, one-time Jobs, tests, and credentials
are not recursively adopted. The source uses sparse checkout only for
`k8Deployment/kubernetes/gitops` and `k8Deployment/kubernetes/helm/mailpit`.
Each later component needs a reviewed HelmRelease, suitable reconciliation RBAC,
its required chart source paths, and an explicit entry in the cluster root as
part of its ownership handoff. Aligned source files alone do not add a release
to Flux reconciliation.

## Controllers and security

The generated `gotk-components.yaml` contains the official, version-pinned
installation: CRDs, Services, service accounts, RBAC, network policies, and
four Deployments. Regenerate that file using the command below; do not edit
its generated internals.

- **source-controller** fetches Git and other sources and serves their artifacts.
- **kustomize-controller** applies declarative resource sets and reports health.
- **helm-controller** manages the explicitly adopted native Mailpit Helm release.
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
Namespace-scoped service accounts can be introduced for application
reconciliation during subsequent adoptions.

`prune: true` removes previously managed objects when removed from Git. The
root inventory includes Flux, Mailpit reconciliation RBAC, and the Mailpit
HelmRelease. Mailpit workload objects and Helm revision Secrets belong to
the native Helm release. Removing an active HelmRelease normally uninstalls
that release; treat its removal as a deliberate cleanup operation. Deleting the entire k3d cluster still removes Flux;
the normal host bootstrap must recreate the cluster before this optional
bootstrap is run again. `k3d cluster stop/start` pauses and resumes controllers
along with the other workloads.

## Bootstrap

Prerequisites: an existing supported CloudDSP cluster with `clouddsp-data`
and a verified native Mailpit release for adoption, cluster-admin kubeconfig access,
`kubectl`, `git`, and the Flux CLI (`brew install fluxcd/tap/flux` on macOS).
The node runtime must be able to pull `ghcr.io/fluxcd` controller images.
These four images are separate from the existing CloudDSP image mirror and
are pulled directly from GHCR for this opt-in installation.

The normal host `bootstrap-platform` command in
[`deploy-local.sh`](../scripts/deploy-local.sh) does not install Flux automatically.
After the local platform is ready, check out the published GitOps branch, inspect
the source URL and branch, then run this separate opt-in bootstrap from the
repository root:

```sh
git switch codex/flux-clouddsp-local
./k8Deployment/kubernetes/scripts/gitops/bootstrap-flux.sh
```

On a clone where the branch is not present locally, fetch it first. For a fork,
update the source URL and branch, commit and publish those changes before
running the script. Do not switch branches with unrelated local edits; use a
separate checkout or worktree in that case.

The script rejects untracked or modified install manifests, checks prerequisites,
installs controller resources, waits for CRDs and controller availability, then
creates the Git source and reconciliation resource. It requests reconciliation
and reports Flux health. It does not publish Git automatically.

## Inspect and reconcile

```sh
flux check --context k3d-clouddsp-local
flux get all --context k3d-clouddsp-local --namespace flux-system
kubectl --context k3d-clouddsp-local get pods --namespace flux-system
flux reconcile kustomization flux-system --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

In Headlamp, inspect the `flux-system` namespace. The optional Headlamp Flux
plugin adds source, reconciliation, and HelmRelease views after installation.
Root readiness checks the Flux controller Deployments. Check Mailpit's
HelmRelease readiness separately; it is not inferred from root readiness. Continue using the CloudDSP verification command for application checks.

## Regenerate the official installation

```sh
flux install --export --version=v2.9.6 \
  > k8Deployment/kubernetes/gitops/clusters/clouddsp-local/flux-system/gotk-components.yaml
```

For a Flux upgrade, generate the intended version, review the manifest changes,
validate the Kustomize build, and publish the commit to the GitOps branch. Flux
will reconcile the controller upgrade. Do not introduce image automation or
application HelmReleases without their separate ownership handoffs.

Official references: [installation](https://fluxcd.io/flux/installation/),
[GitRepository](https://fluxcd.io/flux/components/source/gitrepositories/), and
[Kustomization](https://fluxcd.io/flux/components/kustomize/kustomizations/).
