# AGENTS.md

## 1. Project and Architecture

CloudDSP is a cloud-powered, browser-based digital audio workstation. It
separates uploaded audio into stems, extracts MIDI, supports piano-roll
editing, and applies DSP effects through a React client and AWS processing
services. The durable processing architecture is documented in
[`docs/architecture.md`](docs/architecture.md); update that document whenever
a public API, event payload, persistence model, or infrastructure boundary
changes.
Browser transport, Safari performance, and memory-lifecycle decisions are
documented in [`docs/browser-performance.md`](docs/browser-performance.md).
Update both documents whenever the audio-clock, scheduler, virtual timeline,
or sampled-instrument lifecycle changes.

### Technology

- **Frontend:** React 19, Vite, `@tonejs/midi`, `smplr`.
- **DSP and cloud handlers:** Python 3.11+, Boto3, Demucs, Basic Pitch, ADTOF,
  Pedalboard, and yt-dlp.
- **Infrastructure:** CloudFormation, S3, DynamoDB, EventBridge, AWS Batch,
  Lambda, API Gateway HTTP APIs, API Gateway WebSockets, ECR, and Docker.

### Durable job workflow

DynamoDB is the source of truth for processing state. WebSockets are a
low-latency notification mechanism, not durable result storage.

1. The frontend accepts only WAV, MP3, FLAC, M4A, AAC, OGG, Opus, AIFF, and
   WebM source files, checks the exact `File.size`, then calls `POST /jobs`
   with the requested stem mode and source file information.
2. The job API creates a DynamoDB item and returns a `job_id`, an input key, and
   a size-constrained presigned **POST** upload contract. Input keys use
   `uploads/{job_id}/filename`.
3. The browser POSTs the audio to S3 with object metadata including
   `job-id` and `stem-mode`.
4. S3 publishes an Object Created event to EventBridge. Its input transformer
   passes only dynamic event fields such as `INPUT_BUCKET` and `FILE_KEY` to
   the AWS Batch job.
5. The Demucs container calls `HeadObject` to enforce the durable byte cap and
   read S3 metadata, then uses FFprobe to enforce the duration/audio-stream
   check before it starts GPU work. It derives or verifies the `job_id`, selects
   the requested stem mode, updates job state, uploads stems under
   `stems/{job_id}/`, and invokes MIDI extractors directly.
6. Basic Pitch handles pitched stems and ADTOF handles drums. Each writes MIDI
   under `midi/{job_id}/`, updates the same DynamoDB job, and emits a small
   `job_updated` WebSocket notification.
7. The frontend responds to a notification by calling `GET /jobs/{job_id}`.
   That API generates fresh presigned URLs from stored S3 keys and returns the
   complete current snapshot, including the original-upload URL.
8. The frontend calls `GET /jobs` to show a user's saved-job library. The API
   queries the `user_id-updated_at-index` using the authenticated Cognito
   subject, then the browser opens one job through the owner-checked detail
   endpoint. The library is the source of history; never restore an old local
   job queue from browser storage.
9. A user can permanently remove a completed or failed history item with
   `DELETE /jobs/{job_id}`. The Job API verifies the Cognito owner, deletes
   all versions under that job's `uploads/`, `stems/`, and `midi/` prefixes,
   then removes the DynamoDB record. Do not permit deletion while workers are
   processing a job.

For a linked media source, `POST /jobs/link` accepts only HTTPS source-page
hosts in the reviewed `AllowedMediaHosts` allowlist (YouTube, Bilibili, and
SoundCloud by default), creates the same job first, and asynchronously invokes
yt-dlp. The ingestion Lambda repeats that validation and writes only the durable
`uploads/{job_id}/linked-audio.wav` input key, with `job-id` and `stem-mode`
metadata. That write triggers the same S3 → EventBridge → Batch path as a
browser presigned upload. The React link modal calls this endpoint directly;
it waits for the job to publish `original_url` before fetching audio, then
keeps polling/subscribing while Batch produces stems and MIDI. The Job API
returns that URL only after the durable input exists. A yt-dlp job which fails
before upload deliberately has no `original_url`; never make the browser GET
its predetermined but missing S3 key. Do not add a second direct Batch
submission path.

Source limits default to **500 seconds**, **256 MiB** for an uploaded or
yt-dlp-downloaded encoded source, and **128 MiB** for yt-dlp's normalized
44.1 kHz stereo PCM WAV. The yt-dlp worker fetches metadata before download,
rejects known over-limit duration/size, caps unknown-size transfers with a
progress hook, and validates its converted WAV before it can enter S3. Keep the
browser hint, API POST policy, ingestion Lambda, and Batch checks aligned when
changing these values.

Jobs and their source/stem/MIDI artifacts use a **14-day** retention policy.
The Job API hides a record when its `expires_at` timestamp passes; DynamoDB TTL
and versioned-S3 Lifecycle removal run asynchronously. The S3 policy expires
the current object after 14 days and noncurrent versions one day after they
become noncurrent, so do not promise clock-exact deletion timing in the UI.

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

The profile username is stored in Cognito's `preferred_username` attribute and
is only a display name. Authorization, job ownership, and WebSocket
subscriptions must always use the immutable `sub` claim. A pending signup's
email/display name may be stored in browser local storage solely to resume an
email-confirmation dialog; never persist its password or verification code.

### WebSocket lifecycle

- On socket open or reconnect, the client sends `subscribe` for the one job
  currently open in the workspace.
- The WebSocket handler verifies job ownership, records the temporary
  connection/subscription, and acknowledges the current job revision.
- The client sends a heartbeat every two minutes. Heartbeats keep the socket
  active and detect failure; they are not the artifact-retrieval mechanism.
- While work is pending or an expected MIDI URL is absent, the frontend also
  polls `GET /jobs/{job_id}` every five seconds, with retry backoff for 429/5xx
  responses. A missed notification must never leave a track permanently
  pending.
- Connection records are short-lived and TTL-backed. Do not write a connection
  ID into source or stem metadata as a durable callback destination.

## 2. Repository Layout

- `/frontend-react/` — Vite/React application.
  - `src/auth/cognito.js` and `src/components/AuthPanel.jsx` — browser-only
    Cognito User Pool authentication, including a resumable email-confirmation
    dialog and the profile display name.
  - `src/components/StemSplitter/` — stem grid, MIDI status, and popup editor.
  - `src/hooks/AudioMultiTrackPlayer.js` — shared Web Audio transport. It
    serializes fetch/decode work, owns current `AudioBuffer`s, and publishes a
    ref-based audio-clock transport. It starts all tracks against one
    `AudioContext` clock; do not reintroduce independent HTML `<audio>`
    elements for stem playback.
  - `src/hooks/useTransportPlayhead.js` and `useTimelineViewport.js` — direct
    transform-based playhead and tile-quantized static-timeline virtualization.
    They are intentionally outside the React per-frame state path.
  - `src/hooks/useMidiSynth.js` — fixed-interval, audio-clock look-ahead MIDI
    scheduler. It must never scan all notes from a visual-progress render.
  - `src/hooks/useMidiManager.js` and `useInstruments.js` — stable-S3-key MIDI
    loading, compact original-MIDI snapshots, and lazy smplr allocation and
    disposal. Do not construct sampled instruments merely when a MIDI URL
    arrives. MIDI parse and instrument construction use persistent bounded
    queues, and `dispose()` rather than only `stop()` is required when a
    track/job no longer needs an instrument.
  - `src/hooks/` — MIDI parsing/editing, undo history, audio scheduling, and
    drag interactions.
  - `src/utils/DrumMidi.js` — the canonical ADTOF five-voice General MIDI,
    sampler, visual, and mute/solo mapping.
- `/src/DSP/src/Cloud/` — Lambda handlers, Batch entry points, and cloud DSP
  scripts.
  - `job_api.py` — authenticated job creation, saved-job library, snapshots,
    and permanent terminal-job deletion API Lambda.
  - `BatchDemucs.py` — Demucs Batch entry point and downstream MIDI handoff.
  - `LambdaMIDIBasicPitch.py` — pitched-stem MIDI extraction Lambda.
  - `LambdaMIDIADTOF.py` — drum MIDI extraction Lambda.
  - `cloud_job_workflow.py` — shared DynamoDB state and WebSocket-notification
    helpers. Container images must copy this module alongside their handler.
  - `websocket_handler.py` and `websocket_authorizer.py` — authenticated
    subscription/heartbeat lifecycle and Cognito ID-token verification.
  - `lambdazip/` — deployable ZIP artifacts for `job_api`, the WebSocket
    handler, and the WebSocket authorizer. Upload these files to the artifact
    bucket; image Lambdas are published from ECR instead.
  - `LambdaYtDlp.py` — durable linked-source ingestion Lambda. It is invoked
    only by the Job API, not by a WebSocket route, and writes the job's input
    key in the uploads bucket.
  - `LambdaMIDIMadmom.py` is a retained prototype; it is not wired into the
    durable Job API workflow. Do not use its connection-ID callback design for
    new work.
- `/src/DSP/docker/` — deployment Dockerfiles.
  - `stem_split/` runs Demucs in AWS Batch on GPU.
  - `basic_pitch/`, `adtof/`, and `yt-dlp/` are Lambda container images used
    by the deployed pipeline. The yt-dlp image must copy
    `cloud_job_workflow.py` with its handler. `madmom/` remains an unprovisioned
    prototype image.
- `/IaC/` — componentized CloudFormation templates.
  - `foundation.yaml` — S3 buckets, ECR repositories, CORS, and EventBridge
    delivery from uploads.
  - `jobs.yaml` — durable jobs table, TTL, recovery, and user-job index.
  - `api.yaml` — job creation/status HTTP API and its Lambda role.
  - `realtime.yaml` — WebSocket API, subscription/heartbeat handler, and
    ephemeral connection registry.
  - `midi-lambdas.yaml` — Basic Pitch and ADTOF image Lambdas and their role.
  - `ingestion.yaml` — the yt-dlp image Lambda, its least-privilege role, and
    public-media download limits. Its optional `YtDlpProxyUrl` root parameter
    is stored as a KMS-encrypted SSM SecureString; the worker receives only
    `PROXY_SSM_PARAMETER_NAME` and retrieves it at runtime. Never commit a
    proxy URL or credentials in a template. Its Docker image contains Deno plus the
    version-matched yt-dlp EJS solver and curl-cffi browser impersonation for
    current YouTube challenge handling. The Python handler must convert a
    configured browser name such as `chrome` to yt-dlp's `ImpersonateTarget`;
    the CLI string alone causes an assertion at Lambda startup. The image uses
    the Python 3.12/Amazon Linux 2023 Lambda base because current Deno cannot
    run on the older Python 3.11/Amazon Linux 2 glibc.
  - `processing.yaml` — EventBridge-to-Batch target, GPU Demucs resources, and
    processing permissions.
  - `network.yaml` — public GPU Batch subnets, Internet Gateway egress,
    egress-only security group, and S3 gateway endpoint.
  - `cloud-dsp.yaml` — root nested-stack composition template.
- `/docs/job-id-workflow-plan.md` — phased implementation plan and acceptance
  criteria for the durable job architecture.
- `/docs/architecture.md` — complete component, data-flow, deployment, and
  security reference for the implemented architecture.
- `/docs/browser-performance.md` — browser transport, Safari memory, virtual
  timeline, MIDI scheduler, and performance-debugging reference.

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
The repository ignores `.env` and `.env.*` but retains `.env.example`; never
force-add a local environment file.

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
- Build and publish Basic Pitch, ADTOF, and yt-dlp Lambda images for
  `linux/amd64`/Lambda `x86_64`, and publish the Demucs GPU image before
  creating or updating the processing stack. A changed yt-dlp handler requires
  a rebuilt image: an existing ECR tag still contains its old code.
- Lambda asynchronous invocations are at-least-once. The yt-dlp handler may
  mark only a `source_ingestion` job failed; retries of an already failed job
  must return a successful skipped result. Once S3 accepts its WAV, record
  `source_uploaded=true` without overwriting a Batch status that may have
  advanced concurrently.
- A changed ZIP file at the same S3 key does not reliably replace Lambda code.
  Upload the Job API package under a new key (currently
  `job_api-retention-20260816.zip`) and update `JobApiCodeS3Key` when
  deploying Job API changes.

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
  blocks. Each bucket policy must explicitly deny `aws:SecureTransport=false`;
  use presigned URLs for browser transfer.
- AWS Batch GPU workloads currently run in public VPC subnets to avoid an
  always-on NAT Gateway charge. Keep their security group ingress-free, retain
  the S3 gateway endpoint, and use `MinvCpus: 0` so no public Batch host exists
  while idle. This is a cost-optimized development posture; reassess private
  subnets plus interface endpoints or controlled egress before production.
- Do not set a custom IAM service role on the managed Batch compute
  environment. Omit `ServiceRole` so AWS Batch uses its
  `AWSServiceRoleForBatch` service-linked role; the deployment principal
  needs permission to create that service-linked role if it does not exist.
- EventBridge sees S3 event data, not object metadata. Use its transformer for
  bucket/key values; read `stem-mode` and other object metadata inside the
  container with `HeadObject`.
- The current Lambda container images are built for `linux/amd64`; configure
  their Lambda architecture as `x86_64`. The yt-dlp Lambda intentionally is
  not in the Batch VPC so it can download public media; it needs its own
  duration, temporary-storage, and URL-validation limits.
- Lambda memory also determines CPU allocation. Benchmark Basic Pitch and ADTOF
  at realistic audio lengths before changing memory defaults. `/tmp` storage is
  independent of CPU and should be sized only for the downloaded stem and
  outputs.

## 5. Application Rules

- **Never commit secrets.** Do not hardcode AWS credentials, API keys, proxy
  addresses, or deployment-specific endpoints. Use CloudFormation parameters,
  environment variables, or local ignored configuration.
- **Preserve the CSP.** `frontend-react/csp.js` is emitted into built HTML and
  served by Vite. New browser network or media origins require a deliberate
  production-policy review; never add `unsafe-inline`, `unsafe-eval`, or a
  broad script origin to make a feature work. The production host must mirror
  the generated policy as a `Content-Security-Policy` response header.
- **Respect React render cycles.** Never drive audio position, MIDI scheduling,
  auto-scroll, or a playhead through a `setState()` on every animation frame.
  `AudioMultiTrackPlayer.transportRef` is the authoritative visual/scheduler
  clock; `useTransportPlayhead` owns direct transform writes, and React only
  receives a low-frequency whole-second readout state. Do not reintroduce the heuristic
  `usePlayheadScroll` recovery loop.
- **Keep timeline work viewport-bounded.** Large MIDI files must be rendered
  through the tile-quantized visible range, memoized rows, and simple note
  paint. Avoid per-note shadows, off-screen DOM nodes, or full-note scans in a
  scroll/playback callback. When a scroll surface can be conditionally mounted,
  give `useTimelineViewport` its returned callback ref: changing a mutable
  `ref.current` after an effect ran does not attach its scroll observer. Mount
  the popup editor only while it is open, and do not keep the occluded main MIDI
  grid mounted behind it.
- **Audio transport is Web Audio.** Fetch and decode each current source/stem
  URL once, serialize complete fetch/decode operations, and preserve a usable
  URL across polling snapshots. Schedule every `AudioBufferSourceNode` at the
  same future context time and use per-track `GainNode`s for immediate
  mute/solo/MIDI replacement. New transport code must keep MIDI scheduling
  aligned to that same start time, cancel stale loads, and release raw/dead
  buffers promptly. Do not start separate decode queues when a polling snapshot
  adds one more artifact. The decoded/application representation is the cache;
  do not retain duplicate raw S3 responses in a browser HTTP cache.
- **Stable artifact identity.** Presigned URL query strings are disposable.
  Cache/download/parse S3 objects by stable host plus decoded path, not the full
  URL, otherwise polling replays expensive work and inflates browser memory.
- **Direct source uploads are POST-only.** `POST /jobs` must return the
  size-constrained presigned POST fields and the browser must reject any
  retired presigned-PUT-shaped response. Do not restore an `upload_headers`
  fallback: S3's signed `content-length-range` is the ingestion boundary.
- **History is server-backed.** The selected saved job is held in React state
  only. On reload, fetch the authenticated account's job library; do not cache
  job IDs, artifacts, or presigned URLs in session/local storage.
- **MIDI edit snapshots.** Preserve the original MIDI as an immutable compact
  binary snapshot and never mutate it or an undo snapshot. The active editable
  Tone.js instance may be changed only with a new top-level state object so the
  renderer, scheduler, and undo system rebuild their derived indexes safely.
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
