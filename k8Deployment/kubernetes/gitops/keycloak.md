# Keycloak delivery through Flux

Flux selects [flux-system/clouddsp-keycloak](clusters/clouddsp-local/keycloak/helmrelease.yaml)
and reuses native release `clouddsp-data/clouddsp-keycloak` with its existing
history. The unchanged [chart](../helm/keycloak/) owns only a Deployment,
ClusterIP Service and browser Ingress. Chart `0.1.0`, Keycloak `26.7.3`, the
locked multi-platform image, default values, probes, resources, Secret references,
selector, public issuer and Pod template are preserved.

## Source and delivery order

Prepare and verify the sparse source archive and delivery RBAC before enabling
the HelmRelease. The archive must contain all six reviewed Keycloak chart files.
Publish to `codex/flux-clouddsp-local`. Revision strategy packages the Git SHA
and values generation with the base version. The only values file is the chart's
reviewed default; credentials and external values sources are excluded.

Keycloak waits for PostgreSQL and Mailpit, matching its existing isolated
database and SMTP dependencies. Frontend and Job API now wait for Keycloak.
Job API retains its PostgreSQL/MinIO dependencies; other consumers inherit the
identity gate through Job API. These readiness gates do not create a database,
provision credentials, configure SMTP, create clients or establish realm policies.

Ordinary fresh deployment still initializes the native platform and performs
its guarded realm/client bootstrap before the separate opt-in Flux bootstrap.
The latter verifies existing delivery, database/grants, administrator credentials
and realm/client configuration. Flux has no adopt-only install mode: missing
native Helm storage can cause an install attempt. Prepare the existing platform
before enabling this reconciliation root.

## Durable identity state and lifecycle

Keycloak has no PVC: its realms, users, credentials, clients, keys and durable
sessions belong to the isolated PostgreSQL database. The chart does not own
PostgreSQL, database or administrator Secrets, realm/client/SMTP bootstrap Jobs,
users or identity configuration. Adoption does not reinitialize that database,
run realm bootstrap, reset passwords, rotate signing keys or change clients.

The metadata-only handoff preserves the running Pod UID and container identity,
all three workload/network resource UIDs/specs, and PostgreSQL storage identity.
The one-replica `Recreate` strategy remains unchanged. A later Pod-template or
image update deliberately interrupts this local single-node identity service;
readiness dependencies do not provide high availability.

The issuer remains `http://keycloak.localhost:8080/realms/clouddsp`; the frontend
client keeps its exact callback/origin, public-client Authorization Code flow
and required S256 PKCE. The Job API audience mapper, disabled password/implicit/
service-account grants, registration/email verification/password form and
internal Mailpit SMTP settings remain separately verified bootstrap state.

Removing an active HelmRelease can uninstall the Deployment, Service and route,
interrupting login and discovery despite retained PostgreSQL data. Deleting the
k3d cluster removes its local database. Fresh deployment does not restore data.

### Application login lifetime

The `clouddsp` realm's idle and absolute SSO limits are seven days (604800
seconds), including Remember me sessions. Client session timeouts inherit the
realm limits. Access tokens remain five minutes (300 seconds), and React uses
the refresh token on protected requests. The master realm keeps its existing
administrator timeouts. The source verifier also rejects conflicting timeout
overrides on `clouddsp-react`.

Select **Remember me** on the Keycloak login form to retain its SSO cookie
across browser restarts. React's tokens stay in per-tab `sessionStorage`, so
a newly opened tab may still show Sign in; the retained Keycloak SSO cookie
allows the PKCE redirect to return without a password prompt until expiry.
An already expired refresh token requires one new login after this migration.
Sign-out and administrator revocation still end the session early.

Fresh bootstrap includes the dedicated
[session-policy Job](../services/keycloak/keycloak-realm-session-policy-job.yaml).
For an existing realm, commit the reviewed policy first, then apply only that
policy with the non-interactive migration mode:

```sh
ruby k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-realm-stage.rb apply-session-policy
ruby k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-config-verify.rb verify
```

The migration performs a server dry run, requires its fixed-name Job to be
absent, and verifies the entire durable configuration after completion. It
creates no user, rotates no credential, and does not recreate the realm.
Successful Jobs expire after five minutes; retain failed Jobs for diagnosis.
These settings follow [Keycloak's session timeout and Remember Me semantics](https://www.keycloak.org/docs/latest/server_admin/#_timeouts).

## Delivery permissions and runtime security

The [dedicated identity](clusters/clouddsp-local/keycloak/reconciliation-rbac.yaml)
can mutate only the named Keycloak Deployment, Service and Ingress in
`clouddsp-data`. Create permissions are kind-wide because Kubernetes cannot
restrict create with `resourceNames`. Pods and `apps` ReplicaSets are read-only.
No direct StatefulSet/PVC/PV access, Pod/Job creation, exec, NetworkPolicy,
namespace, CRD, application Deployment or cluster RBAC rights are granted.

Helm history needs namespace-wide Secret CRUD, including data-service
credentials. Deployment writes can create Pods mounting other namespace Secrets
or claims. These direct API restrictions do not isolate this delivery identity
from the data services: it is trusted identity-service administration. Root Flux
and writers to its Git branch retain administrative authority.

Runtime Keycloak remains non-root, drops capabilities, disables privilege
escalation and mounts no API token. Its restricted database login stays distinct
from PostgreSQL administration. The Service and Ingress expose application port
8080; health/metrics management port 9000 remains private. HTTP and unauthenticated
SMTP are local development choices, not a production TLS/email configuration.

Force, takeover, failed-upgrade cleanup, automatic rollback/uninstall remediation
and Helm test hooks are disabled. `serverSideApply: auto` preserves the native
apply behavior; full drift correction has no broad spec or Pod-template exception.

## Verify, smoke and reconcile

From the repository root:

```sh
flux get helmreleases --context k3d-clouddsp-local --namespace flux-system
helm --kube-context k3d-clouddsp-local history clouddsp-keycloak --namespace clouddsp-data
ruby k8Deployment/kubernetes/scripts/releases/keycloak-release.rb verify
ruby k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-database-stage.rb verify
ruby k8Deployment/kubernetes/scripts/stages/credentials/keycloak-admin-secret-stage.rb verify
ruby k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-config-verify.rb verify
ruby k8Deployment/kubernetes/scripts/releases/keycloak-release.rb smoke
```

The [ownership-aware runner](../scripts/releases/keycloak-release.rb) checks
current-generation Flux Ready, the exact defaulted HelmRelease spec, source and
values selection, native target/storage/revision, full source/render/stored/live
parity, Ready Pod and discovery through the browser route. The locked image is
an OCI index: a runtime may report the ARM64 child digest, so single-platform
image-ID equality is not enabled. Handoff snapshots also compare running images.

The state verifier authenticates privately through the Admin API, compares
reviewed realm/client/audience/SMTP/registration settings, and checks the public
issuer. It stores no token file and changes no realm/client configuration.
The database stage separately verifies ownership, grants and credential login.
Administrator token issuance and public login-form requests may create ephemeral
authentication state; they do not provision application identities.

The native smoke is a disposable credential-free discovery Job through internal
Service DNS. The separate existing
[React PKCE smoke](../tests/keycloak-smoke/keycloak-frontend-oidc-authorize-smoke-job.yaml)
checks the application realm and reaches the login form with a valid S256
request. It does not authenticate a user or exchange a token. Require fixed-name
Jobs to be absent before a new run; remove only successful Jobs and retain
failures for diagnosis. Email verification and authenticated API smoke remain
separate workflows with temporary identity cleanup.

Native install/adopt stop whenever this HelmRelease exists, including failed,
suspended or deleting states. Lookup failures fail closed. Explicit absence
preserves the original native fresh-install/adoption gates. Repair and publish
Git changes, then request reconciliation:

```sh
flux reconcile helmrelease clouddsp-keycloak --with-source \
  --context k3d-clouddsp-local --namespace flux-system
```

Inspect conditions, native history and Pod events while preserving resources and
identity state. Do not use realm bootstrap or password reset to repair a delivery
problem. Pause/resume remains `k3d cluster stop/start clouddsp-local`.
