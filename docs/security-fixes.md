# CloudDSP security fixes

This register records remediated findings from
[`security.md`](security.md). A finding keeps its original **SEC** identifier
so its remediation remains traceable to the assessment. **Fixed** means the
specified remediation is implemented in the repository; it does not imply that
all residual risk has disappeared.

## SEC-04 — Fixed — strict linked-media source allowlist

**Fixed on:** 2026-08-15
**Original finding:** [SEC-04 in the security assessment](security.md#sec-04--fixed--medium--linked-media-url-validation-does-not-fully-contain-ssrf)

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

**Original finding:** [SEC-06 in the security assessment](security.md#sec-06--fixed--medium--s3-retention-was-not-aligned-with-job-retention)

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

**Original finding:** [SEC-08 in the security assessment](security.md#sec-08--fixed--medium--browser-session-material-lacked-xss-containment)

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

This is XSS containment, not a replacement for HttpOnly session cookies. A
same-origin script that already runs can still read Cognito's browser storage.
The static built HTML enforces the policy even on basic static hosting, but the
production host must also send the exact production policy as a
`Content-Security-Policy` response header. A header is also required for
`frame-ancestors` to prevent CloudDSP itself from being framed. This repository
has no frontend hosting component, so configure that header in the existing
host/CDN.

## SEC-10 — Fixed — encrypted Parameter Store proxy credential

**Fixed on:** 2026-08-16

**Original finding:** [SEC-10 in the security assessment](security.md#sec-10--fixed--medium--proxy-credentials-were-plaintext-lambda-configuration)

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

**Original finding:** [SEC-11 in the security assessment](security.md#sec-11--fixed--legacy-presigned-put-upload-fallback-weakened-size-enforcement)

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

**Original finding:** [SEC-13 in the security assessment](security.md#sec-13--fixed--tracked-frontend-env-invited-future-secret-exposure)

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

## SEC-14 — Partially fixed — enforce TLS for audio buckets

**Fixed on:** 2026-08-16

**Original finding:** [SEC-14 in the security assessment](security.md#sec-14--partially-fixed--low--s3-tls-enforcement-added-audit-visibility-remains-out-of-scope)

### Implemented control

The uploads and processed-audio buckets each have a bucket-policy explicit
deny for `s3:*` where `aws:SecureTransport` is `false`. The statement covers
the bucket ARN and every object under it, and takes precedence over any IAM or
presigned-URL allow. Browser uploads/downloads and AWS SDK calls already use
HTTPS, but this policy makes transport encryption a non-bypassable S3 boundary
for future callers as well.

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
