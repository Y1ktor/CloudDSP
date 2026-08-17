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
