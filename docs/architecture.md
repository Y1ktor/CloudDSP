# CloudDSP architecture

## Purpose and core model

CloudDSP is a browser-based DAW with an asynchronous AWS audio-processing
pipeline. A signed-in user uploads one source track, AWS Batch separates it
into stems with Demucs, then Lambda functions independently produce MIDI for
the stems. The React client displays and edits the resulting audio and MIDI.

The central architecture decision is:

> DynamoDB is the durable source of truth for a processing job. S3 stores
> binary files. WebSockets only signal that the browser should fetch the
> current job snapshot.

Therefore a page reload, a Batch cold start, a broken socket, a duplicate
notification, an expired presigned URL, or a disconnected client must not
cause an already-produced result to disappear.

## Design principles

- Create an opaque **job_id** before a user uploads source audio.
- Use that job ID in every source key, generated artifact key, DynamoDB record,
  Batch/Lambda payload, log line, and browser subscription.
- Store S3 keys, status, and errors in DynamoDB; do not store expiring
  presigned URLs as canonical results.
- Authenticate the browser with Cognito. Authorize job reads and subscriptions
  with the authenticated Cognito subject rather than a client-supplied user ID.
- Persist an artifact and its DynamoDB state before emitting **job_updated**.
- Treat S3 events, EventBridge, asynchronous Lambda invocation, and WebSockets
  as at-least-once or best-effort transports.
- Keep GPU-intensive Demucs work in AWS Batch. Keep short CPU MIDI extraction
  work in Lambda container images.

## System context

~~~mermaid
flowchart LR
    Browser["React / Vite browser client"]
    Cognito["Cognito User Pool"]
    HttpApi["API Gateway HTTP API<br/>JWT authorizer"]
    JobApi["Job API Lambda"]
    Jobs["DynamoDB Jobs<br/>durable state"]
    Uploads["Private S3 uploads bucket"]
    EventBridge["EventBridge upload rule"]
    Batch["AWS Batch GPU<br/>Demucs container"]
    Artifacts["Private S3 processed-artifacts bucket"]
    BasicPitch["Basic Pitch Lambda<br/>CPU / x86_64 image"]
    ADTOF["ADTOF Lambda<br/>CPU / x86_64 image"]
    SocketApi["API Gateway WebSocket API"]
    SocketAuth["WebSocket JWT authorizer Lambda"]
    Connections["DynamoDB Connections<br/>temporary subscriptions"]

    Browser <-->|"sign up / sign in<br/>ID token"| Cognito
    Browser -->|"POST /jobs, GET /jobs/id<br/>Bearer ID token"| HttpApi
    HttpApi --> JobApi
    JobApi <--> Jobs
    JobApi -->|"presigned PUT URL"| Browser
    Browser -->|"PUT uploads/job_id/file"| Uploads
    Uploads --> EventBridge --> Batch
    Batch <--> Jobs
    Batch -->|"write stems/job_id/*"| Artifacts
    Batch -->|"async invoke per stem"| BasicPitch
    Batch -->|"async invoke drums"| ADTOF
    BasicPitch <--> Jobs
    ADTOF <--> Jobs
    BasicPitch -->|"write midi/job_id/*"| Artifacts
    ADTOF -->|"write midi/job_id/*"| Artifacts
    Browser -->|"WSS ?token=ID token<br/>subscribe / heartbeat"| SocketApi
    SocketApi --> SocketAuth
    SocketApi --> Connections
    SocketApi --> Jobs
    Batch -. "job_updated" .-> SocketApi
    BasicPitch -. "job_updated" .-> SocketApi
    ADTOF -. "job_updated" .-> SocketApi
    Browser -->|"presigned GET URLs"| Artifacts
~~~

## Components

| Area | Resources and code | Responsibility |
| --- | --- | --- |
| Browser | **frontend-react**, **App.jsx**, StemSplitter components | Authenticates, creates jobs, uploads audio, hydrates job snapshots, reconnects WebSockets, polls pending jobs, and renders/edits audio and MIDI. |
| Authentication | **IaC/auth.yaml**, Cognito User Pool | Provides email/password accounts and ID tokens. There is no Cognito Identity Pool and no browser AWS credentials. |
| Job API | **IaC/api.yaml**, **job_api.py** | Creates durable jobs, issues signed upload URLs, enforces ownership, and renders stored artifact keys as fresh signed downloads. |
| Jobs store | **IaC/jobs.yaml**, CloudDSPJobs | Stores the job owner, requested mode, state, artifact keys, error, revision, and expiry. |
| Source storage | Foundation uploads bucket | Holds original user audio and publishes Object Created events to EventBridge. |
| Event routing | Processing EventBridge rule | Matches the uploads prefix and submits a GPU Batch job with source bucket/key overrides. |
| Stem processing | **BatchDemucs.py** in AWS Batch | Validates the job, runs Demucs, uploads stems, persists their state, and asynchronously dispatches MIDI extractors. |
| MIDI processing | **LambdaMIDIBasicPitch.py**, **LambdaMIDIADTOF.py** | Produces pitched MIDI for non-drum stems and drum MIDI for the drums stem. |
| Artifact storage | Foundation processed-audio bucket | Holds stems, MIDI, and BPM JSON. It remains private; the browser gets presigned GET URLs only. |
| Real-time delivery | **IaC/realtime.yaml**, WebSocket handlers | Authenticates connections, records subscriptions, accepts heartbeats, and posts compact update hints. |
| Connection registry | CloudDSPConnections | Stores current connection IDs and subscribed jobs. It is TTL-backed and is never the authority for results. |
| Registry and network | ECR, **IaC/network.yaml** | Hosts deployable images and provides private Batch networking, an S3 gateway endpoint, and NAT egress. |

## Identity and authorization

### Cognito User Pool

The auth component creates a Cognito User Pool and a public browser app client.
The browser uses **amazon-cognito-identity-js** through
**src/auth/cognito.js**. **AuthPanel.jsx** implements registration,
confirmation, sign-in, and sign-out.

The public app client ID may be placed in Vite environment configuration. It
is an application identifier, not a secret. No Identity Pool exists, so a
browser cannot receive AWS IAM credentials. Browser S3 access is limited to
individual presigned URLs.

The current template uses email/password login, verified email, a 12-character
minimum password policy, 60-minute ID/access tokens, and a seven-day refresh
token. Cognito uses the Lite tier by default.

### Job HTTP API

The HTTP API has a native API Gateway JWT authorizer. The browser sends a
Cognito **ID token** in its Authorization header:

~~~http
Authorization: Bearer <Cognito ID token>
~~~

API Gateway validates token issuer and audience before calling **job_api.py**.
The handler reads **sub** from the authorizer context and never trusts a user
ID from a request body. A job that does not exist, or belongs to another user,
returns 404 so the API does not reveal job ownership.

### WebSocket API

A browser WebSocket opening handshake cannot add an arbitrary Authorization
header. The frontend appends the short-lived Cognito ID token as the **token**
query parameter on the WSS endpoint. The custom Lambda authorizer validates:

- Cognito's signing key and RS256 signature
- issuer and browser-client audience
- expiration and required claims
- **token_use** equals **id**

It returns the Cognito subject as API Gateway's **principalId**. The authorizer
and client must not log the token. WSS encrypts the connection, but a query
token is still sensitive and must not be placed in analytics URLs or error
messages. API Gateway does not support authorizer-result caching for WebSocket
APIs, so the token is validated when each connection is established.

## Durable data model

### CloudDSPJobs

The Jobs table uses **job_id** as its partition key. It has the
**user_id-updated_at-index** global secondary index for a future recent-jobs
view. It is on-demand, encrypted, protected by point-in-time recovery, and
uses **expires_at** for TTL cleanup.

An illustrative item:

~~~json
{
  "job_id": "0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7",
  "user_id": "cognito-subject",
  "status": "midi_processing",
  "input_bucket": "clouddsp-uploads-account-region",
  "input_key": "uploads/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/song.wav",
  "source_filename": "song.wav",
  "source_content_type": "audio/wav",
  "stem_mode": "6-stems",
  "stems": {
    "drums": {
      "status": "ready",
      "s3_key": "stems/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums.wav"
    }
  },
  "midi": {
    "drums": {
      "status": "ready",
      "extractor": "adtof",
      "s3_key": "midi/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums.mid",
      "bpm_key": "midi/0f0bb9f6-4c98-4d38-8fea-b7d7f700c5f7/drums_bpm.json",
      "tempo_candidate": {
        "bpm": 128.0,
        "beat_count": 92,
        "drum_event_count": 240,
        "interval_consistency": 0.94,
        "credible": true,
        "source": "adtof_drums"
      }
    }
  },
  "tempo": {
    "bpm": 128.0,
    "source": "adtof_drums",
    "confidence": "high",
    "candidate_count": 1,
    "contributing_stems": ["drums"]
  },
  "revision": 8,
  "created_at": "2026-08-05T12:00:00Z",
  "updated_at": "2026-08-05T12:04:20Z",
  "expires_at": 1780000000
}
~~~

The Job API defaults the item expiry to seven days after creation. DynamoDB TTL
deletion is asynchronous, so it is cleanup rather than an exact retention
clock. The foundation stack currently expires noncurrent S3 versions, but not
the current source/artifact version. Align current-object retention with the
job TTL and privacy policy before production data is stored long term.

| Status | Meaning |
| --- | --- |
| **upload_pending** | Job exists; source upload has not yet reached Batch. |
| **stem_processing** | Demucs has validated the job and is processing it. |
| **midi_processing** | Stems are durable and at least one MIDI extractor is queued or processing. |
| **completed** | All MIDI states are ready. |
| **failed** | The Batch job failed, or MIDI work reached a terminal set with a failure. |

Every durable mutation increments **revision**. It is a monotonic version hint,
not a sequence that must be received without gaps. Clients may fetch the same
or a later revision multiple times safely.

### CloudDSPConnections

The Connections table uses **connection_id** as its partition key and
**user_id-last_seen_at-index** to find active connections for a user. A row
contains the owner, subscribed **job_ids**, connection and heartbeat times, and
**expires_at**. Its nominal TTL is 12 hours.

The disconnect route removes a row immediately. Workers tolerate stale records:
a GoneException during notification is logged and DynamoDB TTL eventually
cleans the record. This table must never contain permanent result state or act
as proof that a job belongs to a user.

## Browser-facing contracts

### Create job

**POST /jobs** requires the ID token. The browser sends:

~~~json
{
  "filename": "song.wav",
  "stem_mode": "6-stems",
  "content_type": "audio/wav"
}
~~~

Allowed stem modes are **2-stems**, **4-stems**, and **6-stems**; the default
is **6-stems**. The response is 201 and includes:

~~~json
{
  "job_id": "uuid",
  "status": "upload_pending",
  "revision": 1,
  "input_key": "uploads/uuid/song.wav",
  "upload_url": "https://presigned-put-url",
  "upload_headers": {
    "Content-Type": "audio/wav",
    "x-amz-meta-job-id": "uuid",
    "x-amz-meta-stem-mode": "6-stems"
  }
}
~~~

The frontend must PUT the file to **upload_url** with every returned header.
Those headers are part of the signature. Changing content type or omitting
metadata will make S3 reject the upload.

### Read job snapshot

**GET /jobs/{job_id}** returns only an authorized job. It removes internal
source bucket/key and TTL fields. Each persisted **s3_key** becomes a fresh
presigned **url**, and each **bpm_key** becomes a fresh **bpm_url**. Download
URLs are valid for one hour by default. The top-level **tempo** object is the
backend-selected master tempo; it is returned directly and never requires a
client to download or vote on BPM artifacts.

The frontend retains job IDs, not presigned URLs. It requests a new snapshot
after a reload, a notification, polling, or a download-link expiry.

### WebSocket messages

After WSS connects, the client sends one subscription per active job:

~~~json
{ "action": "subscribe", "job_id": "uuid" }
~~~

The handler checks ownership, stores the subscription, and replies:

~~~json
{ "type": "job_updated", "job_id": "uuid", "revision": 8 }
~~~

The client also sends **{ "action": "heartbeat" }**. The handler refreshes the
connection expiry and replies with **heartbeat_ack**. An invalid route or
subscription returns an **error** message.

Workers send the same compact **job_updated** message. They never send a MIDI
or stem URL over a socket. The client must respond by reading the HTTP snapshot.

## Processing sequence

~~~mermaid
sequenceDiagram
    participant UI as Browser
    participant API as Job API
    participant Jobs as DynamoDB Jobs
    participant S3 as S3
    participant EB as EventBridge
    participant Batch as Demucs Batch
    participant MIDI as MIDI Lambdas
    participant WS as WebSocket API

    UI->>API: POST /jobs with Cognito ID token
    API->>Jobs: create upload_pending item
    API-->>UI: job_id, key, signed PUT URL and headers
    UI->>S3: PUT uploads/job_id/file with signed metadata
    S3->>EB: Object Created
    EB->>Batch: INPUT_BUCKET and FILE_KEY overrides
    Batch->>Jobs: validate item; set stem_processing
    Batch->>S3: write stems/job_id/stem.wav
    Batch->>Jobs: persist stems; initialize MIDI states
    Batch->>MIDI: async invoke one extractor per stem
    MIDI->>Jobs: mark item processing
    MIDI->>S3: write midi/job_id/stem.mid and BPM JSON
    MIDI->>Jobs: persist ready or failed artifact state
    Batch-->>WS: job_updated after durable mutation
    MIDI-->>WS: job_updated after durable mutation
    WS-->>UI: job_updated(job_id, revision)
    UI->>API: GET /jobs/job_id
    API-->>UI: current snapshot with fresh URLs
~~~

The steps in detail:

1. The user signs in. **App.jsx** restores active job IDs from
   per-user session storage.
2. The client calls **POST /jobs**, records the returned job ID locally, and
   uploads to exactly **uploads/{job_id}/{filename}**.
3. The S3 object metadata carries **job-id** and **stem-mode**. It provides an
   integrity/transition check, but DynamoDB remains authoritative.
4. S3 sends Object Created to EventBridge. The rule filters on the uploads
   bucket and the **uploads/** prefix. EventBridge sees bucket/key event
   fields; it cannot inspect object metadata.
5. EventBridge submits the Batch job. Its transformer sets **INPUT_BUCKET** and
   **FILE_KEY** dynamically. The job definition provides output bucket and a
   fallback stem mode.
6. **BatchDemucs.py** derives the job ID from the source key, consistently
   reads the job, validates the stored source bucket/key, reads S3 metadata,
   and uses the durable stem mode if metadata disagrees.
7. Demucs runs **htdemucs** for 2/4 stems or **htdemucs_6s** for six stems. The
   worker uploads output to **stems/{job_id}/{stem}.wav**, with job/stem
   metadata.
8. One DynamoDB update records all ready stems, initializes every MIDI entry as
   queued, and changes the overall status to **midi_processing**.
9. For each actual output stem, the Batch task asynchronously invokes ADTOF
   when the stem name is **drums**, otherwise Basic Pitch. The payload contains
   job ID, processed bucket, stem key/name, and extractor. A dispatch failure
   is persisted on that individual MIDI state.
10. A MIDI Lambda confirms the stem key matches the durable job record, records
    **processing**, and uses a job-and-stem-scoped temporary directory.
11. Basic Pitch produces melodic MIDI and records a Librosa tempo candidate.
    ADTOF runs its CPU drum model with configurable FPS/thresholds, counts its
    predicted drum events, and records a drum tempo candidate only when both
    drum-event and beat-tracking evidence are sufficient. Both write MIDI plus
    a BPM JSON artifact; failed tempo analysis never discards successful MIDI.
12. Each Lambda writes its artifacts under **midi/{job_id}/**, persists the
    result as ready or failed, and recomputes the backend-owned top-level
    **tempo** field. A credible ADTOF drum candidate wins; otherwise credible
    non-vocal candidates are normalized for clear half/double-time values,
    clustered within 3 BPM (or 3%), and resolved using a weighted median. If
    no credible candidate exists, the durable fallback is 120 BPM with
    **confidence=unknown**. The worker then derives final job status when all
    MIDI work is terminal and emits a best-effort update.

## Frontend behavior

**App.jsx** owns CloudDSP processing orchestration. It supplies snapshots to
the pre-existing DAW components rather than letting UI components call legacy
socket endpoints directly.

- Configuration is supplied through **VITE_COGNITO_USER_POOL_ID**,
  **VITE_COGNITO_BROWSER_CLIENT_ID**, **VITE_JOB_API_URL**, and
  **VITE_WEBSOCKET_URL**. **frontend-react/.env.example** lists the variables.
  Vite embeds all VITE values in the browser bundle, so they must not contain
  secrets.
- The client sends every active job ID after initial socket open and after a
  reconnect. Reconnection uses backoff.
- Pending jobs are polled about every 15 seconds in addition to WebSocket
  delivery. Polling makes correct recovery independent of socket uptime.
- Ready stems appear as soon as their URLs arrive. The MIDI canvas and popup
  editor display processing status until that particular MIDI artifact is ready.
  Failed MIDI is displayed as failed instead of an indefinite loading state.
- MIDI edit operations deep-clone tonejs MIDI data before mutation so track
  state and undo snapshots remain immutable. High-frequency playhead work is
  kept out of normal React state where possible.

The frontend is a Vite single-page application. It does not use server-side
rendering or React Server Components.

## Storage and compute

### S3 and artifact naming

| Bucket | Key format | Writer and reader |
| --- | --- | --- |
| uploads | **uploads/{job_id}/{filename}** | Browser writes with a presigned PUT; Batch reads. |
| processed audio | **stems/{job_id}/{stem}.wav** | Batch writes; browser reads through signed GET. |
| processed audio | **midi/{job_id}/{stem}.mid** and **midi/{job_id}/{stem}_bpm.json** | MIDI Lambdas write; browser reads through signed GET. |

Both buckets are private, AES-256 encrypted, versioned, bucket-owner-enforced,
and protected with public-access blocks. CORS permits configured frontend
origins. Stack deletion/replacement retains these buckets. Noncurrent versions
expire after 30 days and incomplete multipart uploads abort after seven days.

### Batch GPU environment

The network stack creates a VPC with two private Batch subnets in separate
Availability Zones, an egress-only security group, an S3 Gateway endpoint, and
one NAT Gateway. The endpoint keeps S3 traffic off NAT; NAT still provides ECR
image pulls, CloudWatch Logs, Lambda invocation, and other HTTPS egress.

The processing stack defines a managed EC2 GPU compute environment:

- Default instance type: **g4dn.xlarge**.
- Minimum and desired capacity: zero vCPUs. This reduces idle costs but
  introduces instance-provisioning and image-pull cold starts.
- Maximum capacity: eight vCPUs by default.
- Demucs task: one GPU, four vCPUs, 14,336 MiB memory, and one-hour timeout.
- Batch job retry strategy: one attempt.

The compute environment omits a custom service role. For a managed
environment, AWS Batch uses or creates its **AWSServiceRoleForBatch**
service-linked role, which is the supported role for capacity management and
future infrastructure updates.

The single NAT Gateway is a cost optimization and a single-AZ dependency.
Evaluate per-AZ NAT gateways and/or AWS interface endpoints for a
high-availability production environment.

### MIDI Lambda images and ECR

Basic Pitch and ADTOF are image-package Lambda functions using x86_64. Their
images must be built for **linux/amd64**. Both default to 4,096 MiB memory and
1,024 MiB ephemeral storage. Basic Pitch defaults to a 600-second timeout;
ADTOF uses 300 seconds. Lambda memory also provides CPU allocation, so benchmark
with representative stem duration before tuning cost/performance.

The images use job-specific directories beneath /tmp and remove them before or
after inference, avoiding warm-Lambda output collisions. ECR repositories scan
on push, remove untagged images after seven days, and retain the newest ten
images.

## Infrastructure composition and deployment

The root template **IaC/cloud-dsp.yaml** composes the components below and
passes stack outputs rather than embedding bucket names, ARNs, API IDs, or
regions in code.

~~~mermaid
flowchart TD
    Foundation["foundation<br/>S3 + ECR"]
    Jobs["jobs<br/>DynamoDB Jobs"]
    Auth["auth<br/>Cognito"]
    Network["network<br/>VPC + NAT + S3 endpoint"]
    Realtime["realtime<br/>WebSocket + Connections"]
    Midi["midi-lambdas<br/>Basic Pitch + ADTOF"]
    Processing["processing<br/>Batch + EventBridge"]
    Api["api<br/>HTTP Job API"]

    Jobs --> Realtime
    Auth --> Realtime
    Foundation --> Midi
    Jobs --> Midi
    Realtime --> Midi
    Foundation --> Processing
    Jobs --> Processing
    Realtime --> Processing
    Network --> Processing
    Midi --> Processing
    Foundation --> Api
    Jobs --> Api
    Auth --> Api
~~~

The root stack expects two deployment locations outside these components:

1. A template bucket containing nested CloudFormation YAML under a configurable
   prefix.
2. An artifact bucket containing zip packages for the Job API, WebSocket
   handler, and WebSocket authorizer.

Zip packages must contain local dependencies. Package **cloud_job_workflow.py**
with any handler that imports it. Package **PyJWT[crypto]** with the WebSocket
authorizer for Lambda ARM64 Linux; local macOS packages are not suitable for
that runtime. Build/push Demucs, Basic Pitch, and ADTOF images before updating
the stacks that reference their tags or immutable digests.

### First deployment bootstrap

Container images introduce a deliberate two-phase deployment. An ECR repository
must exist before an image can be pushed, while Lambda and Batch cannot be
created from an image tag that does not exist yet.

1. Upload the nested templates and ZIP Lambda artifacts to the template and
   artifact buckets.
2. Create the root stack with **DeployProcessingWorkers=false**. This creates
   Foundation (including ECR), Jobs, Auth, Network, Realtime, and the Job API;
   it intentionally skips the image-based MIDI Lambdas and Batch processing
   stack.
3. Build and push the Basic Pitch, ADTOF, and Demucs images to the Foundation
   ECR repositories with the desired tags.
4. Update the same root stack with **DeployProcessingWorkers=true** and those
   image tags. CloudFormation then creates the MIDI Lambda and Batch
   processing nested stacks.

The root outputs for the Basic Pitch and ADTOF Lambda ARNs and Demucs queue are
absent during the bootstrap phase and appear after worker processing is
enabled. This avoids a circular requirement without manually creating CloudDSP
ECR repositories in the console.

Use **ProjectName** (default **clouddsp**) and **EnvironmentName** (default
**dev**) consistently. IAM role and inline policy names include both. S3/ECR
resource names must meet lowercase naming rules. Configure deployed frontend
origins in **AllowedFrontendOrigins** before release.

## Security controls

- Job ownership is enforced at both HTTP read and WebSocket subscription time.
- Browser access is limited to API requests authenticated by Cognito and
  presigned, narrow S3 object transfers.
- S3 object permissions are scoped to required buckets/prefixes; workers do not
  receive unrestricted S3 access.
- Batch only invokes the named MIDI Lambda ARNs. Lambda/Batch notification
  permissions are scoped to the configured WebSocket API/stage.
- S3 and DynamoDB data stores are encrypted; browser object access uses
  short-lived URLs, and component roles are scoped with least-privilege IAM.
- Never hardcode AWS credentials, secret keys, endpoint-specific credentials,
  or tokens in source or a Vite environment file.

## Failure and recovery behavior

| Condition | Current behavior | Expected result |
| --- | --- | --- |
| Browser reload or socket disconnect | Restore active IDs from session storage, reconnect, resubscribe, and poll snapshots. | Existing results reappear without rerunning DSP. |
| Missed or duplicate update | Update is only a snapshot-fetch hint. | Duplicate/out-of-order WebSocket messages are harmless. |
| Expired artifact URL | API generates a new URL from stored key. | Refresh the job snapshot. |
| Worker exception caught in code | Batch/Lambda persists job or MIDI failure before notifying. | UI can display a job/stem error. |
| EventBridge cannot submit Batch | Retries for up to one hour, at most 24 attempts, then writes to processing SQS DLQ. | Requires operational inspection/redrive. |
| Stale socket | Disconnect, GoneException, or TTL cleanup removes its record. | Durable state is unaffected. |

## Operational limitations and next hardening

The durable job architecture fixes the earlier connection-ID design: results no
longer depend on the particular browser connection that existed at upload time.
Some production hardening remains:

1. **Duplicate Batch execution:** current workers tolerate duplicate delivery at
   the snapshot/UI level, but Demucs does not yet obtain a conditional
   processing lease before running. An at-least-once S3/EventBridge delivery can
   start duplicate GPU work for the same job. Add a conditional
   upload_pending-to-stem_processing claim or a lease token before scaled
   production use.
2. **Monitoring:** add CloudWatch alarms and a runbook for queue backlog,
   Batch failures, Lambda errors/throttles, EventBridge DLQ messages, and jobs
   stuck in non-terminal status. A Batch task that never starts cannot update
   DynamoDB from inside its Python code.
3. **Retention/privacy:** define current-object S3 lifecycle expiry and
   deletion/erasure behavior consistent with seven-day job TTL and product
   privacy requirements.
4. **Deployment verification:** package zip dependencies and images in CI, then
   test real Cognito authentication, CORS, upload, Batch, MIDI, reconnect, and
   URL-expiry paths in a deployed environment.
5. **Cross-device history:** add a user-job listing API backed by the existing
   GSI when jobs must survive beyond one browser session/device.
6. **Cost and availability:** benchmark Lambda memory settings and real audio
   duration, tune Batch capacity, and revisit the single-NAT design before a
   highly available production launch.

## Operations checklist

- Correlate worker and API logs by **job_id**, not connection ID.
- Inspect the EventBridge-to-Batch DLQ when a job remains upload_pending.
- Inspect Batch logs when a job remains stem_processing; inspect MIDI Lambda
  logs and job MIDI entries when one stem fails.
- Regenerate URLs by reading a job snapshot rather than creating permanent URL
  storage.
- Treat ECR scan findings and frontend dependency audits as deliberate,
  tested maintenance work; do not use unreviewed bulk upgrade commands.
