# Kubernetes-local frontend baseline provenance

## Why this copy exists

This directory is the local Kubernetes frontend variant. It begins as a
reviewed snapshot of the cloud React application so that the local deployment
can replace cloud-only integrations (Cognito, AWS API Gateway, and the cloud
WebSocket endpoint) without changing the preserved cloud deployment source.

The copy is intentional, not a generated build directory. Its future
Kubernetes-only source changes are committed and reviewed here.

## Baseline record

| Field | Recorded value |
| --- | --- |
| Source repository path | `cloudDeployment/frontend-react/` |
| Source Git commit | `6393ae3eb4153b883baf993e7c1d887252f8de1f` |
| Source commit subject | `feat: add local frontend oidc client` |
| Copy method | Git object archive of the recorded commit |
| Copied scope | Every frontend file tracked at that commit |
| Working-tree changes used | None |

Using a Git object archive matters: it excludes the source working tree's
uncommitted changes and local artifacts. This local variant is therefore
reproducible from the commit above.

## Configuration boundary

The source cloud tree had an untracked `.env.production` file. It was
**intentionally not copied**. A local `app/.env.production` is also ignored by
Git and will be created only when the local OIDC adapter has a defined set of
public browser settings.

Values prefixed `VITE_` are bundled into JavaScript and readable by every
browser user. They may identify public endpoints or a public OIDC client, but
they must never contain a password, private API key, client secret, database
credential, queue credential, MinIO credential, or Kubernetes credential.

## Synchronization policy

1. Treat `cloudDeployment/frontend-react/` and this `app/` directory as
   separate runtime variants after this baseline.
2. Before importing a cloud UI change, review the cloud commit and its diff;
   copy only the relevant files into `app/` and record the new source revision
   here.
3. Before sending a local UI improvement back to the cloud tree, review it for
   Keycloak, localhost, Kubernetes, and local-service assumptions. Do not copy
   those assumptions into the cloud deployment.
4. Never synchronize `node_modules`, `dist`, `.env*` files other than the
   tracked `.env.example`, temporary test output, or a developer's uncommitted
   changes.

## Current functional state

The local-only `src/auth/oidc.js` adapter now replaces the copied Cognito
boundary. It implements Keycloak Authorization Code with S256 PKCE and uses an
ignored local `.env.production` file for **public** browser configuration. It
does not modify the cloud source tree.

The adapter is browser-integrated in Kubernetes. The local image is served by
the frontend Deployment, ClusterIP Service, and Traefik Ingress at
`http://clouddsp.localhost:8080/`. End-to-end registration, Mailpit email
verification, password creation, and the PKCE callback have passed without
modifying the cloud source tree.
