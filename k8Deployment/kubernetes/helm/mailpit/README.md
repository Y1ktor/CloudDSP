# Mailpit Helm release

This chart owns four existing local objects in `clouddsp-data`:
`Deployment/clouddsp-mailpit`, `Service/clouddsp-mailpit-smtp`,
`Service/clouddsp-mailpit`, and `Ingress/clouddsp-mailpit`. The SMTP Service
remains internal at port 1025. The Ingress routes only the browser UI through
`mailpit.localhost:8080`. Mailpit's inbox uses a Pod-local `emptyDir`, so a Pod
replacement loses captured test mail.

The chart was copied from the legacy manifests in
[`../../services/mailpit/`](../../services/mailpit/). Its rendered object specs
must remain equal to those manifests during adoption. Only top-level ownership
metadata, the release namespace, the locked image reference, and the local
Ingress hostname are Helm expressions. The Pod template labels and immutable
Deployment selector remain exactly the same, so an ownership-only install
does not replace the Pod. The chart creates no namespace, Secret, PVC, or test
Job.

## One-time adoption trial

From the repository root:

```bash
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb plan
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb adopt
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb verify
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb smoke
```

`plan` lints and renders the chart, checks the Mailpit image against
[`../../images.lock.yaml`](../../images.lock.yaml), validates the rendered
objects with a Kubernetes server-side dry run, and compares every declared
spec field with the four live objects. It requires the explicit
`k3d-clouddsp-local` context and refuses unknown owners or an unrelated
release. `adopt` repeats these gates, uses Helm's `--take-ownership` only for
this handoff, then verifies unchanged resource UIDs, Service IPs, and Pod UID.
The existing `kubectl` field manager owns the old `managed-by` labels, so
the script also uses `--force-conflicts` after the exact spec comparison. The
script does not use automatic rollback: uninstalling an adopted release may
delete the original Mailpit objects. A failed first revision is inspected and
then upgraded in place only if it is the expected `mailpit-0.1.0` release and
all four live objects still have their original owner and specs.

`verify` checks the installed release manifest against the reviewed chart,
deployed Helm ownership, spec parity, Pod readiness, and the HTTP ingress
route. `smoke` uses the versioned
[`mailpit-smtp-capture-smoke-job.yaml`](../../tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml)
to send one harmless local test message through the SMTP Service and find it
through the HTTP Service. It removes the Job after success; a failed Job stays
for inspection.

On 2026-09-26, the local trial completed as release `clouddsp-mailpit`
revision 2. The first revision failed on the `kubectl` field-manager conflict
without changing any of the four resource UIDs or ownership labels. The
reviewed retry succeeded, preserved both Service IPs and the existing Pod UID,
returned HTTP 200 through Traefik, and passed the SMTP capture smoke test.
This outcome covers the local Mailpit boundary only; other CloudDSP resources
still need their own charts and adoption checks.

## Fresh-cluster install

On an empty target cluster, the root partial bootstrap runs preparation,
Mailpit installation, and Mailpit verification in one command:

```bash
./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-mailpit
```

After a separate guarded foundation and image preparation stage has created
the `clouddsp-data` namespace, the equivalent component commands are:

```bash
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb install
./k8Deployment/kubernetes/scripts/releases/mailpit-release.rb verify
```

`install` uses the same reviewed chart and image checks but requires the Helm
release and all four Mailpit objects to be absent. It uses ordinary Helm
install without `--take-ownership` or `--force-conflicts`, waits for readiness,
then checks the stored release manifest and local HTTP route. A failed first
attempt is retained for inspection and is not automatically retried over a
partial release. This path has isolated guard tests; the current live cluster
was retained, so a clean-cluster install trial remains outstanding.

After adoption, use the chart and a separately reviewed normal Helm upgrade
for changes to Mailpit. Do not reapply the old raw `services/mailpit/`
manifests. Keep the namespace, KEDA, and Traefik releases with their existing
owners.
