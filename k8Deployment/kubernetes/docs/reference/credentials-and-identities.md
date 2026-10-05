# Credentials and service identity helpers

Start with the [operator guide](../../scripts/README.md). This reference
explains the individual credential stages used by the full bootstrap; it is
not a list of stages to rerun against an existing cluster.

## Source initialization

The value-free [catalog](../../credentials/catalog.yaml) contains 24 groups:
one runtime source per group and nine additional bootstrap sources, for 33
ignored files under `k8Deployment/.local/`. Each source is checked against its
committed example identity, namespace, labels, type, and field names.

```bash
ruby ./k8Deployment/kubernetes/scripts/lib/credential-catalog.rb
./k8Deployment/kubernetes/scripts/deploy-local.sh secrets-init
```

The catalog validator reads committed contracts only. Initialization creates
fixed service identities, independent generated passwords/keys, and matching
runtime/bootstrap values. MinIO's AMQP URL is derived from its restricted
RabbitMQ identity. A complete set is reused after validation; a partial set
stops without filling missing files or changing existing values.

For chosen values, copy [overrides.example.yaml](../../credentials/overrides.example.yaml)
to a private owner-only file and pass `secrets-init --input PATH` before the
first initialization. Only `default` and `generate` fields may be overridden;
fixed identities stay fixed. Custom passwords and generated secret-key fields
need at least 16 characters. The new directory uses mode `700` and files use
`600`. Existing broader permissions are reported, rather than silently fixed.
The deployment owner may inspect their private files; helper output suppresses
credential values.

## Individual runtime Secret stages

These helpers accept `plan`, `bootstrap`, and `verify`:

| Script under `kubernetes/scripts/stages/credentials/` | Source/contract |
| --- | --- |
| [postgresql-secret-stage.rb](../../scripts/stages/credentials/postgresql-secret-stage.rb) | PostgreSQL administrator runtime Secret. |
| [rabbitmq-secret-stage.rb](../../scripts/stages/credentials/rabbitmq-secret-stage.rb) | RabbitMQ administrator runtime Secret. |
| [minio-root-secret-stage.rb](../../scripts/stages/credentials/minio-root-secret-stage.rb) | MinIO root runtime Secret. |
| [minio-amqp-secret-stage.rb](../../scripts/stages/credentials/minio-amqp-secret-stage.rb) | Restricted source-notification AMQP URL; linked RabbitMQ identity. |
| [keycloak-admin-secret-stage.rb](../../scripts/stages/credentials/keycloak-admin-secret-stage.rb) | Keycloak bootstrap administrator Secret. |
| [job-api-database-secret-stage.rb](../../scripts/stages/credentials/job-api-database-secret-stage.rb) | Job API runtime and bootstrap database credentials must agree. |
| [job-api-minio-secret-stage.rb](../../scripts/stages/credentials/job-api-minio-secret-stage.rb) | Job API runtime and provisioning MinIO credentials must agree. |
| [upload-intake-rabbitmq-secret-stage.rb](../../scripts/stages/credentials/upload-intake-rabbitmq-secret-stage.rb) | Restricted source-consumer runtime and provisioning credentials. |
| [upload-intake-minio-secret-stage.rb](../../scripts/stages/credentials/upload-intake-minio-secret-stage.rb) | Intake runtime/provisioning MinIO key pair. |
| [demucs-minio-secret-stage.rb](../../scripts/stages/credentials/demucs-minio-secret-stage.rb) | Demucs runtime/provisioning MinIO key pair. |
| [basic-pitch-minio-secret-stage.rb](../../scripts/stages/credentials/basic-pitch-minio-secret-stage.rb) | Basic Pitch runtime/provisioning MinIO key pair. |
| [adtof-minio-secret-stage.rb](../../scripts/stages/credentials/adtof-minio-secret-stage.rb) | ADTOF runtime/provisioning MinIO key pair. |

Example focused read-only check:

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/job-api-minio-secret-stage.rb verify
```

`plan` validates source contracts and requires the live Secret to be absent.
`bootstrap` performs an API-server dry run, creates only that absent Secret,
and compares the result in memory. Existing or partial live state stops fresh
creation. `verify` compares an existing Secret with its ignored source without
printing or rotating values. Secret creation is separate from provisioning a
matching database role, RabbitMQ account, or MinIO IAM user.

## PostgreSQL and RabbitMQ application identities

[application-identity-stage.rb](../../scripts/stages/credentials/application-identity-stage.rb)
accepts `database|rabbitmq IDENTITY plan|bootstrap|verify`.

| Backend | Supported identities |
| --- | --- |
| PostgreSQL | `upload-intake`, `dispatcher`, `demucs`, `keda-demucs`. |
| RabbitMQ | `dispatcher`, `demucs`, `basic-pitch`, `adtof`, `keda-scaler`. |

```bash
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb database demucs verify
ruby ./k8Deployment/kubernetes/scripts/stages/credentials/application-identity-stage.rb rabbitmq keda-scaler verify
```

Fresh bootstrap creates the runtime Secret and generates its temporary
`clouddsp-data` provisioning copy in memory from the same source. It validates
versioned Jobs, runs them, checks restricted grants/tags/permissions and
login, then removes the temporary Secret. Failed or ambiguous stages remain
for inspection. Verification audits durable account state directly, so
expired completed Jobs are not needed as evidence of success.

Keycloak's database uses [keycloak-database-stage.rb](../../scripts/stages/keycloak/keycloak-database-stage.rb).
Basic Pitch and ADTOF database roles use [worker-database-stage.rb](../../scripts/stages/database/worker-database-stage.rb).
Job API schema ownership, MinIO IAM, and intake's RabbitMQ account use their
separate reviewed runners. See [database/schema](database-and-schema.md) and
[storage/messaging](object-storage-and-messaging.md).

## Browser configuration is separate

The local image builder reads public settings from ignored
`k8Deployment/.local/frontend.env.production`; this file is not one of the 33
Secret sources. Fresh deployment uses the published frontend image and does
not need a host-side frontend build. Vite embeds public settings in browser
JavaScript; administrator passwords, service keys, and client secrets cannot
be protected there. See the [frontend delivery guide](../../services/frontend/README.md).
