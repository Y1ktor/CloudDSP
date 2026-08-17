# CloudDSP security assessment

**Assessment date:** 2026-08-15
**Scope:** Current repository checkout: React/Vite frontend, Python Cloud handlers and packaged ZIPs, CloudFormation, Dockerfiles, dependency lockfiles, and targeted Git secret-history checks. This is a source/configuration review; it does not change runtime behavior.

## Executive summary

CloudDSP has strong application-level controls: private, versioned S3 buckets; Cognito-authenticated HTTP routes; owner checks on job reads, subscriptions, and deletion; constrained presigned **POST** uploads; and worker-side validation of the durable job, S3 object size, and decoded audio.

The important remaining risks are known JavaScript dependency advisories, unrestricted authenticated creation of expensive GPU/media-download work, an overly broad deployment trust boundary, and defense-in-depth gaps around media retrieval, worker isolation, and token handling.

No live AWS state was verified. `aws sts get-caller-identity` could not run because the local AWS session expired. Bucket policies, IAM attachments, CloudTrail, WAF, CloudFront headers, ECR findings, and deployment drift must be reviewed after reauthentication.

## Severity guide

| Severity | Meaning |
| --- | --- |
| High | Practical account/data/cost risk or a known high-severity dependency issue. |
| Medium | Meaningful privacy or defense-in-depth gap to resolve before broad production use. |
| Low | Hardening improvement or a risk that requires an additional weakness. |
| Conditional | Not in the active IaC deployment path, but unsafe if separately deployed. |

## Findings

### SEC-01 — High — JavaScript dependencies have known advisories

`npm audit` reports two high-severity production findings and two more high-severity development/build findings.

| Dependency | Installed | Finding | Patched target |
| --- | --- | --- | --- |
| `react-router-dom` / `react-router` | `7.18.0` | React Router RSC-mode CSRF bypass ([GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2)) | `7.18.2+` |
| `postcss` | `8.5.15` | source-map path traversal / file disclosure advisories | `8.5.26+` |
| `nanoid` | `3.3.15` | unsafe custom-generator infinite-loop advisories | `3.3.18+` |

**Evidence:** `frontend-react/package.json`, `frontend-react/package-lock.json`; `npm audit --omit=dev --json` found the React Router pair, and the full audit found all four. The app uses a client-only `BrowserRouter` and no React Server Components, so present reachability of the RSC advisory is lower than the upstream severity. PostCSS and Nanoid are build dependencies, but should still be updated.

**Remediation:** Make a dependency-only change, review the lockfile, then run `npm audit`, `npm run lint`, and `npm run build`. Do not use an unreviewed blanket `npm audit fix`.

### SEC-02 — High — authenticated users can create unbounded expensive work

Any verified Cognito user can repeatedly create direct or linked jobs. The API validates each object and URL, but it has no per-user active-job limit, cumulative byte quota, daily budget, or application rate limit. The HTTP API stage has no throttling settings, and a linked job can consume a 900-second yt-dlp Lambda before GPU Batch work.

**Evidence:** `src/DSP/src/Cloud/job_api.py:198-350`, `IaC/api.yaml:291-296`, `IaC/ingestion.yaml:69-74`, and `IaC/processing.yaml:203-269`.

**Impact:** An abusive account can create Lambda/S3/Batch cost, queue backlog, and capacity contention. The Batch vCPU cap limits concurrent work but does not prevent a large queued backlog.

**Remediation:** Enforce per-Cognito-`sub` active-job, daily-job, and daily-byte limits with conditional DynamoDB writes; set API route throttles and worker reserved concurrency; alert on abnormal job creation/failure/queue depth; and consider an entitlement or verified-account gate before public linked-media ingestion.

### SEC-03 — High — deployment trust boundary is broader than necessary

The CloudFormation service role has `AdministratorAccess`. The root stack loads nested templates and ZIP functions by mutable S3 key without a repository-defined code-signing control. A party that can update an artifact/template or start a stack update with this role can make account-wide infrastructure changes.

**Evidence:** `IaC/deployment-role.yaml:34-40`, `IaC/cloud-dsp.yaml:17-48`, and `IaC/cloud-dsp.yaml:148-303`.

**Remediation:** Restrict template/artifact bucket writers and `iam:PassRole`/stack-update permission; use immutable versioned artifact keys or S3 versions and record hashes; configure Lambda code signing for ZIP functions; and replace `AdministratorAccess` with a reviewed least-privilege deployment policy after observing required CloudTrail actions. Treat the current role as a privileged deployment boundary.

### SEC-04 — Fixed — Medium — linked-media URL validation does not fully contain SSRF

The API and worker now accept only HTTPS page hosts from the reviewed `ALLOWED_MEDIA_HOSTS` allowlist (YouTube, Bilibili, and SoundCloud by default), reject credentials and non-standard ports, and the worker rejects allowed names resolving to private/reserved addresses. This substantially reduces attacker-controlled destination choice. However, these checks still occur before yt-dlp follows redirects or makes subsequent requests: redirect targets and DNS rebinding are not revalidated/pinned at every hop. The ingestion Lambda is not in a VPC with a filtering egress boundary.

**Evidence:** `src/DSP/src/Cloud/media_url_policy.py`, `src/DSP/src/Cloud/job_api.py`, `src/DSP/src/Cloud/LambdaYtDlp.py`, and `IaC/ingestion.yaml`.

**Remaining remediation:** Route egress through a proxy/DNS firewall that blocks private, link-local, and AWS control-plane destinations; validate every redirect; and set connect/read/redirect limits. Do not rely only on the initial DNS lookup or on the allowlist alone.

### SEC-05 — Medium — worker S3 privileges create a cross-job blast radius

The Demucs role can read/write every processed-artifact object, and MIDI workers have broad get/put access to the same bucket. A compromised worker could read or overwrite another job's output rather than being limited to its normal prefixes.

**Evidence:** `IaC/processing.yaml:158-170` and `IaC/midi-lambdas.yaml:123-127`. Application code normally writes `stems/{job_id}/` and `midi/{job_id}/` only.

**Remediation:** Split permissions by purpose: Demucs needs only the required input reads and stem writes; MIDI workers need stem reads and MIDI writes. For stronger tenant isolation, use scoped per-job credentials or an object-access broker.

### SEC-06 — Fixed — Medium — S3 retention was not aligned with job retention

Jobs and current source/artifact objects now share a 14-day retention policy.
The Job API sets and exposes `expires_at`, hides expired records immediately,
and DynamoDB TTL removes them asynchronously. Both versioned buckets expire
current objects after 14 days, expire noncurrent versions one day after they
become noncurrent, and remove expired delete markers.

**Evidence:** `src/DSP/src/Cloud/job_api.py`, `IaC/api.yaml`,
`IaC/foundation.yaml`, and `frontend-react/src/components/StemSplitter/PreviousJobs.jsx`.

**Residual limitation:** DynamoDB TTL and S3 Lifecycle actions are
asynchronous rather than clock-exact. The application retention boundary is
the `expires_at` timestamp; deployed lifecycle execution should be monitored.

### SEC-07 — High — deployed container images have critical/high CVEs and mutable provenance

Docker Scout scanned the four locally built deployment images and found critical/high CVEs in every image. The raw counts may include duplicate Go standard-library and container-kernel findings, so applicability needs triage, but the result warrants an immediate base/dependency refresh and rescan.

| Image | Critical | High |
| --- | ---: | ---: |
| `clouddsp-basicpitch-lambda:latest` | 2 | 22 |
| `clouddsp-adtof-lambda:latest` | 3 | 22 |
| `clouddsp-demucs:latest` | 23 | 378 |
| `clouddsp-ytdlp:latest` | 2 | 17 |

For example, the ADTOF image pins `torch==2.5.1+cpu`, which Docker Scout reports as affected by `CVE-2025-32434` and fixed in Torch 2.6.0. Demucs is the highest-priority image because it combines the largest CVE count with attacker-controlled media parsing.

All ECR repositories also permit mutable tags and the root stack defaults workers to `latest`. Several Dockerfiles rely on tag-based base images, while the Demucs image installs packages/downloads model weights without version and integrity pinning. The active worker Dockerfiles do not declare an explicit unprivileged `USER`; the Batch worker parses attacker-controlled media.

**Evidence:** Docker Scout scan of the locally built worker images; `IaC/foundation.yaml:146,190,234,278,322`, `IaC/cloud-dsp.yaml:55-73,207-269`, `src/DSP/docker/adtof/Dockerfile:24`, and `src/DSP/docker/stem_split/Dockerfile:4,11-19,27-39`.

**Remediation:** Triage the scan results and rebuild on refreshed bases; update directly affected libraries (including Torch); make ECR tags immutable; deploy digest-pinned images or versioned release tags instead of `latest`; pin base images by digest and packages/models by version and hash; gate releases on image scans/signatures; and run Batch under an explicit non-root user with only required writable directories. Add `.env*`, credential files, PEM keys, and local AWS configuration to `src/DSP/.dockerignore`. Verify Lambda base-image runtime users before relying on inheritance.

### SEC-08 — Fixed — Medium — browser session material lacked XSS containment

The Cognito SDK persists session material in JavaScript-readable browser
storage, so an XSS would still be able to steal it. CloudDSP now emits an
enforced CSP in its built HTML and uses the same policy as a Vite development
and preview response header. It permits scripts only from the app's own origin,
blocks plugins and child frames, restricts fetch/media connections to the
configured API Gateway/Cognito endpoints, S3, and the drum-sample host, and
blocks inline script execution. Inline styles remain permitted because the
React UI currently relies on them.

**Evidence:** `frontend-react/csp.js` and `frontend-react/vite.config.js`.

**Residual limitation:** A CSP reduces the chance of an injected script
running; it cannot protect tokens after a same-origin script, malicious browser
extension, or compromised device already executes. The production static-hosting
layer must mirror the generated production policy as a
`Content-Security-Policy` HTTP header; this is also required to enforce
`frame-ancestors`. No authentication architecture or MFA behavior changed.

### SEC-09 — Medium — the User Pool baseline is intentionally weak

The pool permits an eight-character password with one symbol but does not require lowercase, uppercase, or numeric characters; MFA is disabled. This matches the current product requirement but leaves protection dependent on password quality and Cognito throttling.

**Evidence:** `IaC/auth.yaml:30-63`.

**Remediation:** Offer optional TOTP/WebAuthn MFA immediately and require it for administrative/support accounts. Increase password length and introduce risk-based or compromised-credential protections as public usage grows.

### SEC-10 — Fixed — Medium — proxy credentials were plaintext Lambda configuration

The optional `YtDlpProxyUrl` remains a `NoEcho` deployment input, but the
ingestion stack now writes it through a scoped custom resource to
`/${ProjectName}/${EnvironmentName}/yt-dlp/proxy-url` as a Standard
KMS-encrypted SSM `SecureString`. The yt-dlp worker receives only the
non-sensitive parameter name and calls `GetParameter(WithDecryption=True)` at
runtime. It has `ssm:GetParameter` for that exact parameter and `kms:Decrypt`
for the dedicated customer-managed key; neither the Lambda environment nor the
handler logs contains the URL.

**Evidence:** `IaC/ingestion.yaml`, `IaC/cloud-dsp.yaml`, and
`src/DSP/src/Cloud/LambdaYtDlp.py`.

**Residual limitation:** The yt-dlp process must receive the proxy URL in
memory to use it. An actor with code execution in that Lambda, or with both
the function role and its exact SSM/KMS permissions, can still retrieve it.
CloudFormation's native `AWS::SSM::Parameter` resource cannot create a
`SecureString`, so the template uses a minimal custom resource and does not
log its sensitive input. Proxy credential rotation is provider-specific and is
not falsely automated by this change.

### SEC-11 — Medium, conditional — legacy presigned-PUT upload fallback weakens size enforcement

The current Job API returns a signed POST with an S3 `content-length-range`. React retains a compatibility fallback for an older signed-PUT response. A presigned PUT does not enforce the same signed size range, so an outdated backend could allow an authenticated caller to bypass the upload contract before worker-side rejection.

**Evidence:** `frontend-react/src/App.jsx:655-670`; the current safe contract is in `src/DSP/src/Cloud/job_api.py:238-252`.

**Remediation:** After confirming all environments use the POST-only API, remove the fallback and reject incomplete upload contracts. Retain Batch `HeadObject` size checks as defense in depth.

### SEC-12 — Low — the WebSocket handshake contains a full ID token in its query string

Native browser WebSockets cannot set arbitrary handshake headers, so the app uses `wss://…?token=<Cognito ID token>`. The authorizer correctly validates issuer, audience, expiry, RS256, `sub`, and `token_use`, and does not log the token. It can still appear in access logs, proxies, diagnostics, or browser tooling until expiration.

**Evidence:** `frontend-react/src/App.jsx:425-427`, `src/DSP/src/Cloud/websocket_authorizer.py:34-61`, and `IaC/realtime.yaml:217-225`.

**Remediation:** Ensure log formats exclude query strings and authorization material; keep WSS and short token lifetimes; and consider a short-lived single-use socket ticket minted by the authenticated HTTP API.

### SEC-13 — Low — tracked frontend `.env` invites future secret exposure

`frontend-react/.env` is tracked and is not excluded by `frontend-react/.gitignore`. Its current entries are public browser configuration identifiers/endpoints, not credentials. However, Vite embeds `VITE_*` values in client assets, so this is not safe for secrets and makes an accidental future secret commit more likely.

**Evidence:** `git ls-files frontend-react/.env` and `frontend-react/.gitignore`.

**Remediation:** Remove `.env` from tracking, ignore `.env` and `.env.*` while retaining `.env.example`, and document that all `VITE_*` values are public.

### SEC-14 — Low — TLS enforcement and audit visibility are not defined in the repository

S3 is private, encrypted, versioned, and uses origin-specific CORS, but the templates do not define a bucket-policy deny for `aws:SecureTransport = false`. HTTP and WebSocket stages also have no repository-defined API Gateway access logs. Account-level CloudTrail, S3 data events, WAF, and alerting may exist outside the repository, but could not be verified.

**Evidence:** `IaC/foundation.yaml:27-136`, `IaC/api.yaml:207-304`, and `IaC/realtime.yaml:203-285`.

**Remediation:** Add an explicit S3 TLS deny; configure structured API logs that intentionally omit query strings/tokens; enable CloudTrail data events for both audio buckets; and alert on anomalous deletion, authentication failure, job volume, upload failure, and ECR scan events.

### SEC-15 — Low — internal input hardening gaps

`LambdaYtDlp.py` derives and removes a `/tmp` directory from an event `job_id` before canonical UUID validation and durable job lookup. The HTTP helper also permits an `authorizer.principalId` compatibility fallback rather than only JWT claims. Current IaC restricts invocations to trusted paths, so neither is publicly exposed today.

**Evidence:** `src/DSP/src/Cloud/LambdaYtDlp.py:482-496`, `src/DSP/src/Cloud/job_api.py:104-114`, and `IaC/api.yaml:298-304`.

**Remediation:** Canonicalize `job_id` as a UUID and verify resolved temporary-path containment before cleanup. Remove or explicitly gate the `principalId` fallback in production HTTP API code.

### SEC-16 — Medium — Batch can make unrestricted HTTPS egress through NAT

Batch instances are private and have no security-group ingress, but their security group permits TCP/443 to `0.0.0.0/0` through a NAT gateway. A compromised codec, dependency, or container can therefore exfiltrate audio or credentials to arbitrary HTTPS destinations.

**Evidence:** `IaC/network.yaml:204-216` and `IaC/network.yaml:247-268`.

**Remediation:** Prefer VPC endpoints for S3, ECR, Logs, STS, Lambda, and API Gateway. Where public model/provider access is not required at runtime, remove general NAT egress. Otherwise route it through a controlled egress proxy with domain policy and logging.

### SEC-17 — Conditional high — dormant legacy handlers are unsafe if redeployed

The following files are not referenced by current IaC but bypass the durable job/ownership model.

| File | Concern |
| --- | --- |
| `src/DSP/src/Cloud/presigned_url/lambda-s3-presigned.py` | unauthenticated wildcard-CORS presigned PUT generation without narrow type, size, or key limits |
| `src/DSP/src/Cloud/webSocketAPI/WebSocketNotify.py` | trusts a connection ID and pushes presigned artifact URLs without durable owner verification |
| `src/DSP/src/Cloud/plugin/dsp_bitcrush_flanger_ringmod.py` | insufficiently validated queue/path input and swallowed failures |

**Remediation:** Delete or archive these handlers outside deployable source, or add CI/IaC controls that prevent packaging them. Do not use them as a workflow fallback.

## Verified controls

- All active Job API routes use the API Gateway JWT authorizer.
- Job ownership comes from the Cognito `sub`; reads/deletion enforce ownership and use opaque 404 responses.
- Browser uploads use exact-key, canonical-content-type, metadata, short-expiry, signed POST policies with a content-length range.
- Batch verifies the durable job/key, object size, and FFprobe-readable audio duration before Demucs; it uses an argv list rather than a shell.
- MIDI workers verify each stem key against durable job state.
- The WebSocket authorizer validates issuer, audience, expiry, RS256, `sub`, and `token_use`; subscriptions check ownership.
- S3 buckets are private, versioned, encrypted with SSE-S3, bucket-owner enforced, and use origin-specific CORS. DynamoDB tables have SSE, TTL, and point-in-time recovery.
- Targeted current-tree and Git-history scans found no AWS access-key IDs, private-key blocks, GitHub tokens, Slack tokens, or standard secret-assignment patterns. This is not a replacement for a dedicated secret scanner in CI.

## Commands and limitations

| Check | Result |
| --- | --- |
| `npm audit --omit=dev --json` | 2 high production findings (React Router pair) |
| `npm audit --json` | 4 high total findings (adds PostCSS and Nanoid) |
| `npm audit fix --dry-run --json` | identified the patched versions in SEC-01; no files changed |
| `npm run lint` | completed with pre-existing warnings; no security-lint rule set is configured |
| `python3 -m py_compile` for active Cloud handlers | passed |
| Targeted `git grep` / `git log -G` secret checks | no hits for the stated patterns |
| Docker Scout, four local worker images | critical/high findings recorded in SEC-07 |
| Source/IaC/Docker review | completed manually |
| Live AWS review | not performed because the local AWS session expired |

`semgrep`, `bandit`, `gitleaks`, `trivy`, `checkov`, `cfn-lint`, and `pip-audit` were not available in this environment. Add them to CI, scan published ECR images on every push, and repeat the review against the live account after authentication is restored.

## Remediation order

1. Update JavaScript dependencies and re-run the production audit.
2. Add per-user quotas, throttles, and cost/abuse monitoring.
3. Lock down deployment: immutable artifacts/images, signed releases, and a least-privilege deployment role.
4. Close SSRF, unrestricted egress, and cross-job worker-role gaps.
5. Add browser headers, token/logging protection, TLS enforcement, and secret management.
6. Remove dormant legacy handlers and the old presigned-PUT compatibility path.
