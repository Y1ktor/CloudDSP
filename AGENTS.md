# AGENTS.md

## 1. Project and Architecture

CloudDSP is a cloud-powered, browser-based digital audio workstation. It
separates uploaded audio into stems, extracts MIDI, supports piano-roll
editing, and applies DSP effects through a React client and AWS processing
services. The durable processing architecture is documented in
[`docs/architecture.md`](docs/architecture.md); update that document whenever
a public API, event payload, persistence model, or infrastructure boundary
changes.

### Technology

- **Frontend:** React 19, Vite, `@tonejs/midi`, `smplr`.
- **DSP and cloud handlers:** Python 3.11+, Boto3, Demucs, Basic Pitch, ADTOF,
  Pedalboard, and yt-dlp.
- **Infrastructure:** CloudFormation, S3, DynamoDB, EventBridge, AWS Batch,
  Lambda, API Gateway HTTP APIs, API Gateway WebSockets, ECR, and Docker.

### Durable job workflow

DynamoDB is the source of truth for processing state. WebSockets are a
low-latency notification mechanism, not durable result storage.

1. The frontend calls `POST /jobs` with the requested stem mode and source file
   information.
2. The job API creates a DynamoDB item and returns a `job_id`, an input key, and
   a presigned upload URL. Input keys use `uploads/{job_id}/filename.wav`.
3. The browser uploads the audio to S3 with object metadata including
   `job-id` and `stem-mode`.
4. S3 publishes an Object Created event to EventBridge. Its input transformer
   passes only dynamic event fields such as `INPUT_BUCKET` and `FILE_KEY` to
   the AWS Batch job.
5. The Demucs container calls `HeadObject` to read S3 metadata, derives or
   verifies the `job_id`, selects the requested stem mode, updates job state,
   uploads stems under `stems/{job_id}/`, and invokes MIDI extractors directly.
6. Basic Pitch handles pitched stems and ADTOF handles drums. Each writes MIDI
   under `midi/{job_id}/`, updates the same DynamoDB job, and emits a small
   `job_updated` WebSocket notification.
7. The frontend responds to a notification by calling `GET /jobs/{job_id}`.
   That API generates fresh presigned URLs from stored S3 keys and returns the
   complete current snapshot.

Store S3 keys and status in DynamoDB; never store presigned URLs as the
authoritative artifact value. URLs expire and must be generated when a job is
read.

The browser uses a Cognito **User Pool** only. It never receives AWS
credentials: it calls the Job HTTP API with an ID token and transfers S3
objects only with presigned URLs. The HTTP API's JWT authorizer uses the
Cognito ID token in `Authorization`. The WebSocket `$connect` authorizer uses
the short-lived ID token in the WSS `token` query parameter because browser
WebSockets cannot set arbitrary opening-handshake headers. Do not log that
token.

### WebSocket lifecycle

- On socket open or reconnect, the client sends `subscribe` for every active
  `job_id`.
- The WebSocket handler verifies job ownership, records the temporary
  connection/subscription, and acknowledges the current job revision.
- The client sends a heartbeat every 2–4 minutes. Heartbeats keep the socket
  active and detect failure; they are not the artifact-retrieval mechanism.
- While work is pending, the frontend also polls `GET /jobs/{job_id}` with
  backoff. A missed notification must never leave a track permanently pending.
- Connection records are short-lived and TTL-backed. Do not write a connection
  ID into source or stem metadata as a durable callback destination.

## 2. Repository Layout

- `/frontend-react/` — Vite/React application.
  - `src/auth/cognito.js` and `src/components/AuthPanel.jsx` — browser-only
    Cognito User Pool authentication.
  - `src/components/StemSplitter/` — stem grid, MIDI status, and popup editor.
  - `src/hooks/` — audio scheduling, MIDI parsing/editing, undo history, and
    drag interactions.
- `/src/DSP/src/Cloud/` — Lambda handlers, Batch entry points, and cloud DSP
  scripts.
  - `job_api.py` — authenticated job creation and snapshot API Lambda.
  - `BatchDemucs.py` — Demucs Batch entry point and downstream MIDI handoff.
  - `LambdaMIDIBasicPitch.py` — pitched-stem MIDI extraction Lambda.
  - `LambdaMIDIADTOF.py` — drum MIDI extraction Lambda.
  - `cloud_job_workflow.py` — shared DynamoDB state and WebSocket-notification
    helpers. Container images must copy this module alongside their handler.
  - `websocket_handler.py` and `websocket_authorizer.py` — authenticated
    subscription/heartbeat lifecycle and Cognito ID-token verification.
- `/src/DSP/docker/` — deployment Dockerfiles.
  - `stem_split/` runs Demucs in AWS Batch on GPU.
  - `basic_pitch/` and `adtof/` are Lambda container images.
- `/IaC/` — componentized CloudFormation templates.
  - `foundation.yaml` — S3 buckets, ECR repositories, CORS, and EventBridge
    delivery from uploads.
  - `jobs.yaml` — durable jobs table, TTL, recovery, and user-job index.
  - `api.yaml` — job creation/status HTTP API and its Lambda role.
  - `realtime.yaml` — WebSocket API, subscription/heartbeat handler, and
    ephemeral connection registry.
  - `midi-lambdas.yaml` — Basic Pitch and ADTOF image Lambdas and their role.
  - `processing.yaml` — EventBridge-to-Batch target, GPU Demucs resources, and
    processing permissions.
  - `network.yaml` — private GPU Batch subnets, NAT egress, security group, and
    S3 gateway endpoint.
  - `cloud-dsp.yaml` — root nested-stack composition template.
- `/docs/job-id-workflow-plan.md` — phased implementation plan and acceptance
  criteria for the durable job architecture.
- `/docs/architecture.md` — complete component, data-flow, deployment, and
  security reference for the implemented architecture.

## 3. Local Setup and Validation

### Frontend

```bash
cd frontend-react
npm install
npm run dev
npm run build
```

Copy `frontend-react/.env.example` to a local ignored environment file and set
the Cognito, Job API, and WebSocket values from the deployed stack outputs.
Do not put credentials or private AWS resource values in a `VITE_*` variable:
Vite embeds those values into browser assets.

### DSP

Use the existing Python virtual environment where appropriate, for example
`src/DSP/venv3.11`, and install the dependencies required by the relevant
Docker image before local testing.

### Infrastructure

- Validate CloudFormation YAML before deployment. `cfn-lint` is preferred when
  installed; at minimum run a YAML parser and `git diff --check`.
- Pass stack outputs into dependent component templates. Do not hardcode bucket
  names, table names, ARNs, API IDs, or regions in source code.
- The root `IaC/cloud-dsp.yaml` composes the component stacks. When deploying
  components independently, use this dependency order: foundation, jobs, auth,
  network, realtime, MIDI Lambdas, processing, then the API.
- A clean account needs a two-phase root deployment. First use
  `DeployProcessingWorkers=false` to create foundation/ECR and the non-image
  components. Push the three worker images, then update the same root stack
  with `DeployProcessingWorkers=true`. Do not try to create Lambda image
  functions before their ECR tags exist.
- Package the three zip Lambdas (`job_api`, `websocket_handler`, and
  `websocket_authorizer`) before deployment. The authorizer package must also
  contain the dependencies in `requirements-websocket-authorizer.txt`; build
  native dependencies for Lambda's `arm64` Linux runtime.
- Build and publish Basic Pitch and ADTOF Lambda images for `linux/amd64`/
  Lambda `x86_64`, and publish the Demucs GPU image before creating or updating
  the processing stack.

## 4. Infrastructure Rules

- **No ClickOps.** Make AWS infrastructure changes in `/IaC/`; deploy the
  templates rather than creating long-lived resources manually in the console.
- Use `ProjectName` with default `clouddsp` and `EnvironmentName` with default
  `dev` in every component. S3/ECR names must remain lowercase.
- Every created IAM role and inline IAM policy name must include both
  `${ProjectName}` and `${EnvironmentName}`.
- Use least-privilege IAM. Scope S3 permissions to the required bucket/prefix,
  DynamoDB permissions to the required table/index, Lambda invokes to named
  functions, and `execute-api:ManageConnections` to the specific WebSocket API
  and stage.
- Keep S3 buckets private, encrypted, versioned, and protected by public-access
  blocks. Use presigned URLs for browser transfer.
- AWS Batch GPU workloads run in private VPC subnets. Include an S3 gateway
  endpoint. If private instances do not use NAT, also provide the required
  interface endpoints for ECR API/Docker, CloudWatch Logs, STS, and ECS/AWS
  Batch dependencies before launching jobs.
- Do not set a custom IAM service role on the managed Batch compute
  environment. Omit `ServiceRole` so AWS Batch uses its
  `AWSServiceRoleForBatch` service-linked role; the deployment principal
  needs permission to create that service-linked role if it does not exist.
- EventBridge sees S3 event data, not object metadata. Use its transformer for
  bucket/key values; read `stem-mode` and other object metadata inside the
  container with `HeadObject`.
- The current Lambda container images are built for `linux/amd64`; configure
  their Lambda architecture as `x86_64`.
- Lambda memory also determines CPU allocation. Benchmark Basic Pitch and ADTOF
  at realistic audio lengths before changing memory defaults. `/tmp` storage is
  independent of CPU and should be sized only for the downloaded stem and
  outputs.

## 5. Application Rules

- **Never commit secrets.** Do not hardcode AWS credentials, API keys, proxy
  addresses, or deployment-specific endpoints. Use CloudFormation parameters,
  environment variables, or local ignored configuration.
- **Respect React render cycles.** Do not drive high-frequency audio/playhead
  updates through React state. Use refs and `useLayoutEffect`/direct DOM work
  where appropriate.
- **Immutable MIDI data.** Deep-clone `@tonejs/midi` structures before editing;
  never mutate globally shared MIDI data or undo-history snapshots.
- Treat `job_id` as the correlation key across upload, Batch, stem, MIDI, API,
  and WebSocket code. Do not use a WebSocket connection ID for this purpose.
- Persist a completed artifact to DynamoDB before sending `job_updated`.
  Notifications can be duplicate or out of order; frontend snapshot hydration
  must be idempotent.
- Treat S3, EventBridge, Lambda asynchronous invocation, and WebSockets as
  at-least-once/best-effort transports. Do not make processing correctness rely
  on a single notification or on exactly-once event delivery.
- Surface MIDI download or parsing errors as failed states. Do not leave a
  track displayed indefinitely as "processing" after a URL has arrived.

## 6. Git Workflow

- Use Conventional Commit messages and the `.gitmsg` template.
  Valid prefixes are `feat:`, `fix:`, `refactor:`, and `chore:`.
- Keep commits atomic. Do not mix unrelated refactors with a feature or
  infrastructure change.
- Do not commit local editor configuration, generated artifacts, Docker caches,
  credentials, or image layers.
- Do not run a blanket `npm audit fix` or accept a major dependency upgrade
  without reviewing the lockfile and testing `npm run build`. Treat dependency
  remediation as an intentional, isolated change.
