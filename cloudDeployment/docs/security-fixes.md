# CloudDSP security fixes

This register records remediated findings from
[`security.md`](security.md). A finding keeps its original **SEC** identifier
so its remediation remains traceable to the assessment. **Fixed** means the
specified remediation is implemented in the repository; it does not imply that
all residual risk has disappeared.

## 2026-08-25 reassessment

This scan did not implement another code or infrastructure change. It validates
the controls recorded below against the current source/configuration and the
available local images. The current finding status is maintained in
[security.md](security.md#2026-08-25-reassessment-register); this register
keeps the original implementation rationale and records the change in
assessment.

| Finding | Reassessment result |
| --- | --- |
| SEC-02 | The atomic UTC daily quota implementation is present and working as designed; it remains a submission-count limit, not full abuse control. |
| SEC-03 | AdministratorAccess has been removed. The execution role is CloudDSP-scoped and runtime roles have a maximum-permission boundary; immutable artifact provenance remains a follow-up. |
| SEC-04 | The initial URL allowlist/DNS control is implemented. It is now classified as partially fixed because redirects, DNS rebinding, and unrestricted ingestion egress remain. |
| SEC-05 | Demucs, MIDI, and Job API S3 grants are separated by uploads/stems/MIDI prefix. Shared worker roles still cannot be constrained to one runtime job ID. |
| SEC-06 | The 14-day Job API, DynamoDB, and S3 lifecycle boundary remains implemented. |
| SEC-08 | The CSP is live at the CloudFront-hosted website, together with HSTS, anti-framing, nosniff, and referrer-policy headers. |
| SEC-10 | The SSM SecureString/KMS design remains present in source. Live AWS drift could not be checked because the local AWS session expired. |
| SEC-11 | POST-only browser/API handling is still implemented. S3 CORS retaining an unused PUT method is a cleanup item, not a restored bypass. |
| SEC-13 | Environment-file tracking remains fixed. The history review found expired presigned URLs in old mock/test diffs; see new SEC-19. |
| SEC-14 | TLS deny now covers the uploads, processed-audio, website, and demo-assets buckets; audit visibility remains intentionally deferred. |
| SEC-15 / SEC-16 | The documented deferred temporary-path hardening and accepted public-Batch egress decision are unchanged. |
| SEC-18 / SEC-19 | Newly discovered PyJWT CVEs and historic expired presigned URLs have no remediation yet. |

## SEC-02 — Partially fixed — per-user daily job quotas

**Fixed on:** 2026-08-24
**Original finding:** [SEC-02 in the security assessment](security.md#sec-02--partially-fixed--high--authenticated-users-can-create-expensive-work)

### Implemented control

The Jobs component now owns a separate encrypted, on-demand DynamoDB table
named `${ProjectName}-${EnvironmentName}-DailySubmissionQuotas`. `job_api.py`
uses `TransactWriteItems` so it increments a counter and creates the matching
durable job as one all-or-nothing operation. The key is the authenticated
Cognito `sub` plus the UTC date, which makes five direct-upload jobs and three
yt-dlp linked-media jobs the default per-account limits for every calendar day.
The job is not created when its limit is exhausted; the API returns HTTP 429.

The table has a TTL attribute only to remove old accounting records. A fresh
UTC-date key performs the actual daily reset, so asynchronous DynamoDB TTL
deletion can never extend or shorten a user's quota window.

Successful submissions, quota-exhausted HTTP 429 responses, and the
authenticated saved-job-list response include a read-only snapshot of both
counters, their limits, remaining requests, and the next UTC reset timestamp.
The browser renders that snapshot in the account menu and turns a rejected
submission into a precise quota message. Detail polling deliberately omits the
read so its five-second processing cadence cannot inflate quota-table traffic.

### Residual risk

The control deliberately limits submission count only. It does not yet cap
active jobs, daily bytes, API request rate, Lambda concurrency, or the number
of distinct verified accounts an attacker can create. Those remain required
before an unrestricted public launch.

### Deployment action

Upload a new Job API ZIP containing `job_api.py` and `media_url_policy.py`,
upload `jobs.yaml`, `api.yaml`, and `cloud-dsp.yaml`, then update the root stack
with that new ZIP key. The root parameters default to five
`MaxDailyDirectUploadJobs` and three `MaxDailyYtDlpJobs` per UTC day.

## SEC-03 — Fixed, narrowed residual — bounded CloudFormation and runtime authority

**Fixed on:** 2026-08-25

### Implemented control

`deployment-role.yaml` replaces the bootstrap role's
`AdministratorAccess` attachment with CloudDSP-specific provisioning policies.
It scopes name-addressable resources to the `ProjectName`/`EnvironmentName`
namespace, confines Route 53 record updates to the supplied hosted zone, limits
`iam:PassRole` to CloudDSP runtime roles and the four expected services, and
allows only the two AWS-managed runtime policies actually used by the templates.
The role is explicitly denied any mutation or pass-role use of itself.

The same bootstrap stack creates the
`${ProjectName}-${EnvironmentName}-CloudDSPRuntimeBoundary` managed policy.
Every API, WebSocket, yt-dlp, Batch, Demucs, EventBridge, and MIDI runtime role
declares it as `PermissionsBoundary`. The boundary is the maximum capability;
the individual role policy remains the smaller operational grant. This blocks a
template from escalating a runtime role into general account administration.

### Verification

The deployed `clouddsp-cloudformation-adminRole`, API, realtime, ingestion,
processing, and MIDI stacks reached `UPDATE_COMPLETE`. IAM inspection confirmed
that `AdministratorAccess` is not attached to the execution role and every
active runtime role has the CloudDSP runtime boundary.

### Residual risk

Certain create APIs cannot be resource-scoped by AWS, so narrowly enumerated
EC2, Batch, API Gateway, Cognito, KMS, CloudFront, and ACM actions retain
`Resource: "*"`. Mutable template ZIP/S3 keys and mutable ECR tags remain an
artifact-integrity concern. They can change CloudDSP resources within the
boundary, but cannot recreate general administrator capability through a
runtime role.

## SEC-05 — Partially fixed — purpose-scoped worker artifact access

**Fixed on:** 2026-08-25

### Implemented control

The deployed Demucs task role now has `s3:GetObject` on `uploads/*` and
`s3:PutObject` on `stems/*` only. The shared Basic Pitch/ADTOF role has
`s3:GetObject` on `stems/*` and `s3:PutObject` on `midi/*` only. The Job API
has no broad processed-bucket read: it reads the uploads, stems, and MIDI
prefixes required to issue URLs and delete an owned terminal job.

### Residual risk

The deployed identity is shared by all jobs. It cannot be parameterized by the
event's job ID, so a compromised worker remains able to access a different
valid job prefix within its operational category. Strong tenant isolation would
require per-job STS credentials/session tags or a job-aware object-access
broker.

## SEC-04 — Partially fixed — strict linked-media source allowlist

**Fixed on:** 2026-08-15

**2026-08-25 status:** The allowlist implementation is verified, but the
overall SSRF finding is **partially fixed**, not fully closed. The residual
redirect/DNS/egress limitation below remains material.

**Original finding:** [SEC-04 in the security assessment](security.md#sec-04--partially-fixed--medium--linked-media-url-validation-reduces-but-does-not-eliminate-ssrf)

### Implemented control

`POST /jobs/link` now accepts only credential-free HTTPS page URLs on the
reviewed `ALLOWED_MEDIA_HOSTS` allowlist. The default deployment value permits
YouTube (`youtube.com`, `youtu.be`), Bilibili (`bilibili.com`, `b23.tv`), and
SoundCloud (`soundcloud.com`, `on.soundcloud.com`), including their legitimate
subdomains. It rejects lookalike suffixes, HTTP, embedded credentials, invalid
ports, and HTTPS ports other than 443.

The validation lives in
[`media_url_policy.py`](../src/DSP/src/Cloud/media_url_policy.py). Both the Job
API and the yt-dlp Lambda call it, so direct or asynchronous Lambda invocation
cannot bypass the policy. The yt-dlp Lambda retains its DNS check that rejects
an allowlisted hostname resolving to a private or reserved IP address.

`AllowedMediaHosts` is a root CloudFormation parameter passed to the API and
ingestion components. Adding a provider now requires an explicit stack
parameter change and provider review rather than silently accepting arbitrary
URLs.

### Verification

[`test_media_url_policy.py`](../src/DSP/tests/test_media_url_policy.py) covers
accepted provider and short-link URLs, HTTP and credential rejection,
lookalike-host rejection, port rejection, and an explicitly configured future
provider. Local verification completed with:

```bash
cd cloudDeployment
python3 -m unittest src/DSP/tests/test_media_url_policy.py
python3 -m py_compile src/DSP/src/Cloud/media_url_policy.py \
  src/DSP/src/Cloud/job_api.py src/DSP/src/Cloud/LambdaYtDlp.py
```

### Residual risk

This constrains the browser-supplied *initial page host*; yt-dlp can still
follow a permitted provider's redirects and retrieve media from its CDN. DNS
rebinding and every redirect are not pinned or revalidated at each connection,
and the ingestion Lambda has unrestricted public egress. A controlled egress
proxy or DNS firewall that blocks private, link-local, and AWS control-plane
destinations remains the next defense-in-depth action.

### Deployment action

Upload `job_api-media-url-allowlist-20260815.zip` to the artifact bucket,
rebuild and push the yt-dlp Lambda image, upload the changed nested templates,
and update the root stack. Use the new Job API ZIP key rather than overwriting
the prior artifact.

## SEC-06 — Fixed — aligned job and artifact retention

**Fixed on:** 2026-08-16

**Original finding:** [SEC-06 in the security assessment](security.md#sec-06--fixed--14-day-job-and-artifact-retention-is-aligned)

### Implemented control

The Job API now defaults `JOB_TTL_DAYS` to 14 and receives the same
`JobRetentionDays` CloudFormation parameter that configures the uploads and
processed-audio buckets. New jobs retain a Unix `expires_at` timestamp; the
library endpoint returns it and the history modal renders a short relative
message such as `expires in 9 days`.

Both versioned S3 buckets now expire current objects after 14 days. A current
object expiration creates a delete marker in a versioned bucket, so the policy
also removes noncurrent versions one day after they become noncurrent and
cleans expired delete markers. This closes the prior gap where current objects
could persist indefinitely after their DynamoDB record was hidden.

### Residual limitation

DynamoDB TTL and S3 Lifecycle evaluate records asynchronously. `expires_at`
is the application-visible retention cutoff; AWS may physically remove the
record/object later. The browser should therefore show the remaining retention
window, not promise deletion at an exact clock time.

## SEC-08 — Fixed — CSP-based XSS containment

**Fixed on:** 2026-08-16

**2026-08-25 status:** Verified live at the CloudFront website. The response
includes CSP, HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff, and
the documented referrer policy.

**Original finding:** [SEC-08 in the security assessment](security.md#sec-08--fixed--csp-based-xss-containment-is-live)

### Implemented control

The Vite configuration injects a `Content-Security-Policy` meta element in
the generated HTML and serves the same policy from its development and preview
servers. Production permits scripts only from the CloudDSP origin; it blocks
objects, child frames, and form posts to other origins. It generates exact API
Gateway, WebSocket, and Cognito origins from public build configuration; S3
remains limited to virtual-hosted S3 endpoints because presigned URLs are
dynamic. The reviewed drum-sample host is the only other permitted origin.

The development policy adds only localhost HTTP/WebSocket connectivity so Vite
HMR works; it is never included in a production build. Inline styles remain
allowed because the current React UI uses style properties, but inline scripts
and event-handler attributes are blocked.

### Residual limitation and deployment action

**Reassessment correction:** The repository now has a CloudFront hosting
component and the live production host sends the CSP as an HTTP response
header, so frame-ancestors is enforced. The policy still permits wildcard
virtual-hosted S3 origins for dynamic presigned URLs, and no Permissions-Policy
header is currently sent. Narrow S3 origins where feasible and add a
restrictive permissions policy as defense in depth.

This is XSS containment, not a replacement for HttpOnly session cookies. A
same-origin script that already runs can still read Cognito's browser storage.
The static built HTML remains a useful secondary enforcement point, but the
CloudFront response-header policy is the production enforcement boundary.

## SEC-10 — Fixed — encrypted Parameter Store proxy credential

**Fixed on:** 2026-08-16

**2026-08-25 status:** Source/IaC controls remain verified. The local AWS
session expired before a live parameter/IAM drift check could run.

**Original finding:** [SEC-10 in the security assessment](security.md#sec-10--fixed--proxy-credentials-are-not-plaintext-lambda-configuration)

### Implemented control

The root stack still accepts the optional `YtDlpProxyUrl` as a `NoEcho`
deployment input, but the ingestion nested stack writes it to
`/${ProjectName}/${EnvironmentName}/yt-dlp/proxy-url` as a Standard SSM
`SecureString` encrypted under a dedicated customer-managed KMS key. A custom
resource is necessary because CloudFormation's native `AWS::SSM::Parameter`
resource does not support the `SecureString` type.

The yt-dlp Lambda configuration now contains only
`PROXY_SSM_PARAMETER_NAME`. Its role can call `ssm:GetParameter` for exactly
that path and `kms:Decrypt` for exactly that key. The handler obtains it with
`WithDecryption=True`, validates it in memory, and avoids logging the value or
AWS error details.

### Residual limitation

The credential must exist as plaintext in the Lambda process while yt-dlp
connects to the proxy. This control prevents routine Lambda configuration and
deployment-snapshot readers from obtaining it; it does not protect against
Lambda code execution or principals granted the same narrowly scoped SSM/KMS
permissions. Automatic credential rotation remains intentionally out of scope
until the proxy provider offers a reliable rotation API.

## SEC-11 — Fixed — POST-only direct uploads

**Fixed on:** 2026-08-16

**2026-08-25 status:** The active browser and Job API source still use only
the content-length-constrained POST contract. The bucket CORS rule's unused
PUT allowance should be removed when compatibility permits.

**Original finding:** [SEC-11 in the security assessment](security.md#sec-11--fixed--direct-uploads-are-post-only-and-size-constrained)

### Implemented control

The browser now requires `upload_fields` from `POST /jobs` and sends every
direct source upload as a presigned S3 `POST` multipart form. It no longer
recognizes or sends the retired `upload_headers` / presigned `PUT` contract.
S3 therefore evaluates the signed `content-length-range` before accepting the
source; the API's 256 MiB maximum is enforced before EventBridge or Batch work
can begin.

### Deployment action

Deploy the current frontend assets with the current Job API ZIP. An older
cached frontend bundle has its own fallback code and cannot be changed by a
backend deployment alone.

## SEC-13 — Fixed — local browser configuration is no longer tracked

**Fixed on:** 2026-08-16

**2026-08-25 status:** Environment-file tracking remains fixed. The old
frontend environment file contained only public browser identifiers, but the
history scan separately found expired presigned URLs in old mock/test diffs.
See SEC-19 in security.md.

**Original finding:** [SEC-13 in the security assessment](security.md#sec-13--fixed--frontend-environment-files-are-no-longer-tracked)

### Implemented control

`frontend-react/.env` is now untracked but remains in place locally. Root and
frontend ignore rules exclude `.env` and `.env.*` while explicitly retaining
`.env.example`. The example documents the four expected public configuration
values: Cognito User Pool ID, Cognito browser client ID, Job API URL, and
WebSocket URL.

Vite compiles every `VITE_*` value into browser JavaScript, so these variables
must never contain passwords, tokens, AWS credentials, proxy URLs, or client
secrets. Server-side credentials belong in a runtime secret service such as
the SSM SecureString path used by the yt-dlp proxy, not in any frontend file.

### Residual limitation

Ignoring files does not erase old Git history. The existing tracked file held
only public identifiers/endpoints, so no credential rotation is required for
this cleanup. Revoke and rotate any genuine secret immediately if one is ever
committed in the future.

## SEC-14 — Partially fixed — enforce TLS for IaC-managed S3 buckets

**Fixed on:** 2026-08-16

**2026-08-25 status:** The TLS deny control also covers the website and
demo-assets buckets, not only the two audio buckets.

**Original finding:** [SEC-14 in the security assessment](security.md#sec-14--partially-fixed--tls-is-mandatory-for-iac-managed-s3-buckets)

### Implemented control

The uploads, processed-audio, website, and demo-assets buckets each have a
bucket-policy explicit deny for `s3:*` where `aws:SecureTransport` is `false`. The
statements cover each bucket ARN and every object under it, and take precedence
over any IAM or presigned-URL allow. Browser transfers and AWS SDK calls
already use HTTPS, but this policy makes transport encryption a
non-bypassable S3 boundary for future callers as well.

### Remaining accepted risk

No API Gateway access logging, CloudTrail data-event configuration, WAF, or
alerting is added in this change. Audit visibility remains intentionally out of
scope for now and SEC-14 is therefore only partially fixed.

## SEC-15 — Deferred — job-ID temporary-directory containment

**Reviewed on:** 2026-08-16

### Current risk assessment

`LambdaYtDlp.py` derives the temporary working directory
`/tmp/clouddsp-ytdlp/{job_id}` and clears it before independently parsing the
event value as a UUID or looking up its DynamoDB job. A path-like value such as
`../another-directory` could therefore escape the intended scratch-directory
prefix if an untrusted caller invoked the Lambda directly.

This is low risk in the currently deployed workflow. The only normal caller is
the authenticated Job API, which creates every `job_id` with `uuid.uuid4()` and
asynchronously invokes yt-dlp with that generated value. A normal browser user
cannot choose or alter the Lambda event's job ID. The handler is not exposed as
an HTTP, S3, EventBridge, or WebSocket target, and Lambda execution environments
are isolated from the host filesystem.

### Deferred remediation

Treat this as an internal trust-boundary hardening task before adding another
invoker or reusing the handler. Parse `job_id` with `uuid.UUID`, use its
canonical `str(...)` form when constructing the directory, and verify the
resolved directory remains beneath `/tmp/clouddsp-ytdlp` before either cleanup
call. This prevents a malformed direct invocation, a future integration bug,
or a compromised invoking principal from causing path traversal in Lambda's
writable temporary storage.

## SEC-16 — Accepted architecture decision — public Batch removes idle NAT cost

**Reviewed on:** 2026-08-17

### Cost evaluation

The former single-NAT design incurred roughly **$36.50 per 30-day month** in
fixed `us-east-1` charges: `$0.045/hour` for the NAT Gateway (**$32.85**) plus
`$0.005/hour` for its required public Elastic IP (**$3.65**). It also incurred
the `$0.045/GB` NAT data-processing charge. That baseline continued even when
`MinvCpus` was zero and no Batch job was active.

Replacing NAT fully with private connectivity would require S3's free gateway
endpoint plus interface endpoints for the Batch host and task dependencies
(at least ECR API, ECR Docker, CloudWatch Logs, STS, AWS Batch, ECS control
plane/agent/telemetry, Lambda, and API Gateway; S3 and DynamoDB use free
gateway endpoints). At a representative `$0.01/hour` per interface endpoint
in each of two Availability Zones, ten endpoints would cost about **$146.00
per 30-day month** before endpoint data-processing charges. Actual regional
prices and the endpoint set must be checked before a production decision.

The implemented development configuration removes NAT and keeps the free S3
gateway endpoint. The Batch compute environment has `MinvCpus: 0`, so it has
no idle network charge. Public IPv4 pricing is approximately `$0.005/hour`
only while a Batch EC2 host is running (about `$0.05` for ten host-hours), and
the selected GPU instance price is otherwise unchanged by public versus private
subnet placement.

### Implemented change and accepted risk

`IaC/network.yaml` now launches Batch hosts in two public subnets with Internet
Gateway routes. The old NAT Gateway and Elastic IP are deleted. Batch keeps an
ingress-free security group, HTTPS/DNS-only egress, and an S3 gateway endpoint.
This removes idle NAT spend but does **not** remove general HTTPS egress. A
public IPv4 and unrestricted outbound HTTPS remain the accepted SEC-16 risk;
the production alternative is private Batch subnets with the required interface
endpoints and/or controlled, logged egress.

## Newly observed findings — not yet remediated

### SEC-18 — vulnerable PyJWT in the WebSocket authorizer

The authorizer requirements and deployable ZIP still package PyJWT 2.10.1.
The 2026-08-25 dependency scan found several upstream CVEs fixed by 2.12.0 or
2.13.0. The directly relevant one is CVE-2026-48524: an unauthenticated
connection attempt with an unknown key ID can cause PyJWKClient to refresh the
Cognito JWKS. See SEC-18 in security.md for reachability analysis and the
required Lambda arm64 ZIP rebuild/deployment procedure.

### SEC-19 — expired S3 presigned URLs in repository history

The current tree contains no matching presigned URLs or standard credential
patterns, but old mock/test diffs contain temporary S3 links that had expired
before the reassessment. Add history-aware secret scanning and avoid committing
generated URLs. See SEC-19 in security.md for impact and remediation guidance.
