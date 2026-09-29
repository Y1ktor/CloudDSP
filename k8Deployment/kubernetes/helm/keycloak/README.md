# Keycloak Helm release

This chart owns the existing `Deployment/clouddsp-keycloak`,
`Service/clouddsp-keycloak`, and `Ingress/clouddsp-keycloak` in
`clouddsp-data`. Keycloak stores its durable realms, users, clients, and
sessions in the separately managed PostgreSQL database. The chart does not
own PostgreSQL, either Keycloak Secret, realm/client/SMTP bootstrap Jobs, or
the disposable smoke Jobs.

The templates mirror the three retained manifests in
[`../../services/keycloak/`](../../services/keycloak/). Helm changes only the
namespace and ownership fields while taking over; values copy the reviewed
[`images.keycloak.immutableReference`](../../images.lock.yaml) and existing
public issuer/Ingress host. The Deployment selector, `Recreate` strategy,
Pod probes, management-port isolation, resource limits, Secret references,
Service port, and Ingress route remain identical as Kubernetes objects.
Credential values stay in ignored local Secrets, outside Helm values and
release history.

From the repository root, run:

```bash
./k8Deployment/kubernetes/scripts/keycloak-release.rb plan
./k8Deployment/kubernetes/scripts/keycloak-release.rb adopt
./k8Deployment/kubernetes/scripts/keycloak-release.rb install
./k8Deployment/kubernetes/scripts/keycloak-release.rb verify
./k8Deployment/kubernetes/scripts/keycloak-release.rb smoke
```

`plan` checks strict Helm lint, the image lock, source/render/live spec
equality, and the API-server schema. The one-time `adopt` repeats those gates
before explicit Helm takeover and checks that all three resource UIDs, the
Service IP, and Pod UID remain unchanged. It does not automatically uninstall
or roll back an adopted release. `verify` compares the stored Helm manifest
and live spec with the chart, checks a Ready Pod, and requests the public OIDC
discovery path through the browser Ingress. `smoke` creates the versioned,
unauthenticated [OIDC discovery Job](../../tests/keycloak-smoke/keycloak-oidc-discovery-smoke-job.yaml),
asserts the advertised issuer and endpoints through the ClusterIP Service,
then deletes that exact Job after success.

`install` is the fresh-cluster path. It refuses an existing release or any of
the three named workload objects, verifies the isolated PostgreSQL database
and ignored bootstrap-admin Secret, then performs an ordinary Helm install
without ownership takeover. The fresh Pod can create Keycloak's initial
master-realm administrator and run schema migrations in its dedicated database.
The CloudDSP application realm and clients are configured separately by the
guarded [`keycloak-realm-stage.rb`](../../scripts/keycloak-realm-stage.rb)
after Helm install. That stage runs the committed one-shot Jobs only for an
absent realm and verifies the durable result through the Admin API.

On 2026-09-26, adoption produced `clouddsp-keycloak` revision 1 without
replacing those objects or the Pod. The public discovery route, internal
discovery/issuer Job, React Authorization Code with PKCE login-form Job,
Keycloak-to-Mailpit verification-email Job, and authenticated Job API read Job
all passed. Each disposable Job was removed after success. The email test
deleted its temporary user, while the authenticated-read test deleted its
temporary user and client after validating `/auth/me` and `/jobs`.

Use this chart for reviewed Keycloak delivery changes. Keep the raw manifests
as comparison baselines and do not reapply them to Helm-owned objects. A
future issuer or database change is a separate migration because existing
clients, token validation, and stored identities depend on those contracts.
