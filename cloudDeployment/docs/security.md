# CloudDSP security assessment

**Initial assessment:** 2026-08-15

**Reassessed:** 2026-08-25

**Scope:** React/Vite frontend, Python handlers and deployable ZIPs,
CloudFormation, Dockerfiles, local worker images, dependency lockfiles, and a
targeted Git-history secret review. This is a read-only source/configuration
assessment; it does not alter deployed AWS resources.

## Executive summary

CloudDSP has materially improved since the initial assessment. The 14-day
retention boundary, POST-only direct uploads, proxy credential handling,
frontend environment-file hygiene, S3 TLS-deny policies, daily submission
quotas, and a live CloudFront CSP/header policy are now present.

The remaining priorities are dependency/image remediation and privileged
deployment boundaries. Four high JavaScript advisories remain, every scanned
worker image still has critical/high CVEs, and the deployed WebSocket
authorizer ZIP pins a PyJWT version with a reachable JWKS-refresh amplification
issue. The URL allowlist reduces SSRF substantially but is not a complete
egress control. Runtime roles now have purpose-specific S3 grants and a
CloudDSP permissions boundary, but the shared worker identities still span job
prefixes. Mutable deployment artifacts, query-string WebSocket tokens, and
accepted public-Batch egress remain open design risks.

The assessment names both CVEs/advisories and broader security findings. A
**Fixed** status means the repository contains the intended control; it does
not imply that a related residual risk has disappeared.

## Severity guide

| Severity | Meaning |
| --- | --- |
| High | Practical account, data, cost, or worker-compromise risk; or a known high-impact dependency issue. |
| Medium | Meaningful availability, privacy, or defense-in-depth gap to resolve before broad production use. |
| Low | Hardening improvement or a risk requiring an additional weakness. |
| Conditional | Not in the active IaC deployment path, but unsafe if separately deployed. |

## 2026-08-25 reassessment register

| Finding | Current status | New observation |
| --- | --- | --- |
| SEC-01 | **Open** | The same four high npm-audit advisories remain; the two React Router entries are production dependencies. |
| SEC-02 | **Partially fixed** | Atomic UTC daily quotas are implemented, but active-job, daily-byte, API-stage, and account-abuse limits remain absent. |
| SEC-03 | **Fixed, narrowed residual** | AdministratorAccess is removed. The execution role is restricted to CloudDSP provisioning actions and all runtime roles have a CloudDSP permissions boundary; mutable artifacts and selected create APIs with unavoidable `Resource: "*"` remain. |
| SEC-04 | **Partially fixed** | HTTPS allowlisting, credential/port rejection, and initial DNS checks are implemented; redirect and DNS-rebinding containment is still absent. |
| SEC-05 | **Partially fixed** | Demucs reads uploads and writes stems only; MIDI reads stems and writes MIDI only; the Job API reads only stems/MIDI. Shared roles still span all valid job prefixes. |
| SEC-06 | **Fixed** | Job records and audio artifacts have aligned 14-day application/lifecycle retention. |
| SEC-07 | **Open — high** | Fresh local Scout scans still show critical/high worker-image CVEs. Mutable tags and unpinned provenance remain. |
| SEC-08 | **Fixed, narrowed residual** | The live site sends CSP, HSTS, anti-framing, nosniff, and referrer-policy headers. CSP S3 wildcards and a missing Permissions-Policy remain hardening opportunities. |
| SEC-09 | **Accepted/open** | The intentionally permissive password policy and no-MFA posture are unchanged. |
| SEC-10 | **Fixed** | The proxy is stored as a KMS-encrypted SSM SecureString and retrieved at runtime. |
| SEC-11 | **Fixed** | Direct browser ingestion uses only a content-length-constrained presigned POST. |
| SEC-12 | **Open — low** | A full Cognito ID token still appears in the native WebSocket connection query string. |
| SEC-13 | **Fixed, with historical caveat** | No frontend environment file is tracked now; an expired presigned-URL exposure in Git history is recorded separately as SEC-19. |
| SEC-14 | **Partially fixed** | TLS-deny policies cover the four IaC-managed S3 buckets; audit logging/alerting remains intentionally out of scope. |
| SEC-15 | **Deferred — low** | yt-dlp still removes its event-derived temporary directory before canonical UUID validation. |
| SEC-16 | **Accepted — medium** | Cost-optimized public Batch hosts retain unrestricted outbound HTTPS while running. |
| SEC-17 | **Archived on 2026-10-03** | Legacy upload/callback/effects handlers are retained outside active deployment source and image contexts. Their historical behavior is unchanged. |
| SEC-18 | **New — medium** | The WebSocket authorizer packages vulnerable PyJWT 2.10.1; an unauthenticated unknown JWT key ID can amplify JWKS refreshes. |
| SEC-19 | **New — low historical exposure** | Expired S3 presigned URLs were found only in Git history, not the current tree. |

## Findings

### SEC-01 — High — JavaScript dependencies have known advisories

**Status: Open.** The dependency set has not changed since the prior review.
npm audit --omit=dev --json reports two high production entries and the full
audit reports four high entries.

| Dependency | Installed | Advisory | Patched target |
| --- | --- | --- | --- |
| react-router-dom / react-router | 7.18.0 | React Router RSC-mode CSRF bypass ([GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2)) | 7.18.2+ |
| postcss | 8.5.15 | source-map path traversal / file disclosure ([GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849), [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp)) | 8.5.26+ |
| nanoid | 3.3.15 | unsafe custom-generator infinite loop ([GHSA-28wg-ghj8-5hjv](https://github.com/advisories/GHSA-28wg-ghj8-5hjv), [GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8)) | 3.3.18+ |

The app uses a client-only BrowserRouter and no React Server Components or
server actions, so reachability of the RSC CSRF issue appears lower than the
upstream advisory severity. PostCSS and Nanoid are build dependencies, but the
versions should still be upgraded in a reviewed dependency-only change.

**Remediation:** update the four packages to the patched versions, review the
lockfile, then run npm audit, npm run lint, and npm run build. Do not use an
unreviewed blanket npm audit fix.

### SEC-02 — Partially fixed — High — authenticated users can create expensive work

The Job API now atomically reserves a daily slot and creates the durable job in
one DynamoDB transaction. A verified Cognito subject receives five direct
browser-upload contracts and three linked-media yt-dlp jobs per UTC day.
Counters reset through the date portion of the key, not through eventual TTL
deletion, and quota exhaustion returns HTTP 429.

**Evidence:** job_api.py, jobs.yaml, api.yaml, and the account-menu quota
response/UI.

**Remaining impact:** coordinated accounts can still submit up to their
allowance. There is no active-job or daily-byte quota, API-stage rate/burst
throttle, worker reserved concurrency, or repository-defined abuse alerting.
The Batch vCPU cap limits concurrent GPU work, not queued work.

**Next remediation:** add active-job and daily-byte limits, route throttles,
worker concurrency limits, and monitoring for job-creation/failure/queue-depth
anomalies.

### SEC-03 — Fixed, narrowed residual — High — deployment authority is now bounded

**Status: Fixed, with residual risk.** The bootstrap role no longer attaches
AdministratorAccess. Its CloudDSP-named policies limit provisioning to the
project/environment naming boundary, a configured Route 53 zone, named S3,
DynamoDB, ECR, SQS, Lambda, and EventBridge resources, and an explicit set of
CloudFormation service actions. It can pass only CloudDSP-named runtime roles
to EC2, ECS tasks, EventBridge, or Lambda, and can attach only the two required
AWS service policies.

Every CloudDSP runtime role has the
`clouddsp-dev-CloudDSPRuntimeBoundary` permissions boundary. This prevents a
malicious template or artifact from converting a Lambda, Batch, or EventBridge
role into an administrator role even if it can change that role's inline policy
or trust relationship. The execution role is explicitly denied mutation or
pass-role operations on itself.

**Evidence:** deployed `clouddsp-cloudformation-adminRole`,
IaC/deployment-role.yaml, and the permissions-boundary properties in the API,
realtime, ingestion, processing, and MIDI component templates.

**Residual risk:** some resource-creation APIs do not support resource-level
authorization, so the role retains small, enumerated `Resource: "*"` action
sets for EC2 networking, Batch, API Gateway, Cognito, KMS, CloudFront, and ACM.
Template ZIP/S3 keys and ECR tags are still mutable. An authorized artifact
writer could therefore change CloudDSP resources within the boundary, but no
longer gain general account administration through this execution role.

**Next remediation:** restrict artifact writers, use immutable S3 versions and
ECR digests/code signing, and use CloudTrail evidence to further reduce the
remaining create/read action sets.

### SEC-04 — Partially fixed — Medium — linked-media URL validation reduces but does not eliminate SSRF

The Job API and yt-dlp Lambda now accept only credential-free HTTPS page URLs
from the reviewed media-host allowlist. They reject lookalike hosts,
non-standard ports, literal/private/reserved addresses, and allowlisted names
that resolve to private/reserved addresses at the initial check.

**Evidence:** media_url_policy.py, job_api.py, LambdaYtDlp.py, and
IaC/ingestion.yaml.

**Residual risk:** yt-dlp can follow redirects and make provider/CDN requests
after the initial validation. Redirect destinations are not revalidated and
DNS resolution is not pinned, so redirect abuse/DNS rebinding remains possible.
The ingestion Lambda intentionally has public egress for media retrieval.

**Next remediation:** use controlled egress or DNS filtering that blocks
private, link-local, and AWS control-plane destinations; enforce redirect,
connect, and read limits; and validate every redirect target.

### SEC-05 — Partially fixed — Medium — worker S3 scope is purpose-limited, not job-isolated

**Status: Partially fixed.** The deployed Demucs role can read only
`uploads/*` and write only `stems/*`. The MIDI extraction role can read only
`stems/*` and write only `midi/*`. The Job API can read only the uploads,
stems, and MIDI prefixes needed to create presigned transfers and remove an
owner's terminal job. It no longer has a broad processed-bucket `GetObject`
grant.

**Evidence:** deployed `clouddsp-dev-DemucsAudioObjects`,
`clouddsp-dev-MidiExtractionArtifacts`, and
`clouddsp-dev-JobApiPresignObjects` policies; IaC/processing.yaml,
IaC/midi-lambdas.yaml, and IaC/api.yaml.

**Residual risk:** these are static shared roles. A compromised Demucs worker
can still read another job's valid `uploads/` object and write another job's
`stems/` object; a compromised MIDI worker has the analogous stems/MIDI
cross-job scope. IAM policy variables cannot bind a long-lived Batch/Lambda
role to the event's individual `job_id`.

**Next remediation:** use short-lived per-job STS credentials with session tags
or put S3 access behind a job-aware broker for strong tenant isolation.

### SEC-06 — Fixed — 14-day job and artifact retention is aligned

Jobs expose a durable expires_at timestamp and the Job API hides expired
records. The uploads and processed buckets expire current objects after 14
days, remove noncurrent versions one day after they become noncurrent, and
remove expired delete markers. The history UI communicates the remaining
period.

**Evidence:** job_api.py, api.yaml, foundation.yaml, and PreviousJobs.jsx.

**Residual limitation:** DynamoDB TTL and S3 Lifecycle execution are
asynchronous. expires_at is the application-visible cutoff; AWS may physically
remove data later.

### SEC-07 — High — worker image vulnerabilities and mutable provenance remain

**Status: Open.** Fresh Docker Scout quickviews of the local 08-18 image IDs
still reported the following vulnerability counts:

| Image | Critical | High | Scan status |
| --- | ---: | ---: | --- |
| Basic Pitch | 2 | 12 | freshly scanned |
| ADTOF | 3 | 23 | freshly scanned |
| yt-dlp | 2 | 10 | freshly scanned |
| Demucs | prior result: 23 | prior result: 378 | re-index did not complete; count is stale, not a fresh assertion |

Source pins independently show active affected libraries: ADTOF uses Torch
2.5.1 CPU and Demucs uses PyTorch 2.4.0. Both are affected by
CVE-2025-32434 and CVE-2026-24747 (the latter is fixed in Torch 2.10.0).
Basic Pitch pins scikit-learn 1.3.2, affected by CVE-2024-5206 and fixed in
1.5.0. The observed Basic Pitch inference flow does not train TfidfVectorizer,
and audio uploads do not supply a PyTorch checkpoint, so those individual code
paths are not presently demonstrated as remote-code-execution paths; they
remain inventory/release-gate issues.

All ECR repositories still use mutable tags and root-stack image defaults
remain latest. Active Dockerfiles do not declare an explicit unprivileged USER;
this matters most for the Demucs Batch image that parses attacker-controlled
media. Lambda base images run within the Lambda sandbox, so the Dockerfile
omission alone should not be treated as proof of root Lambda execution.

**Remediation:** triage the exact Scout CVE output; refresh bases and directly
affected dependencies; use immutable ECR tags and digest-pinned deployments;
pin bases/packages/models by digest/version/hash; gate release on image scans;
run Batch as a non-root user; and extend src/DSP/.dockerignore to exclude
environment files, local AWS files, and PEM/key material.

### SEC-08 — Fixed — CSP-based XSS containment is live

The live website and demo manifest now return a Content-Security-Policy, HSTS,
X-Frame-Options: DENY, X-Content-Type-Options: nosniff, and
Referrer-Policy: strict-origin-when-cross-origin. The policy matches
../../frontend/csp.js (cloud profile) and the CloudFront response-headers policy in
IaC/hosting.yaml. Production builds contain no source maps, and the active
React path has no unsafe HTML injection, eval, new Function, or remote script
import.

**Residual limitation:** Cognito tokens remain JavaScript-readable browser
storage, so a same-origin script or malicious extension could still read them.
The policy permits https://*.s3.amazonaws.com for media/connect requests
because presigned URLs are dynamic; passing only the exact CloudDSP bucket
origins at build time would reduce exfiltration options further. The CloudFront
CSP parameter defaults empty, so future deployments must continue to supply
the exact generated header. No Permissions-Policy is returned; adding a
restrictive policy for unused capabilities is low-priority defense in depth.

### SEC-09 — Medium — User Pool baseline is intentionally weak

**Status: Accepted/open.** Cognito still permits an eight-character password
with one symbol and does not require MFA. This is a deliberate product choice,
but leaves protection dependent on password quality and Cognito throttling.

**Evidence:** IaC/auth.yaml.

**Future remediation:** offer TOTP/WebAuthn MFA, require it for
administrative/support accounts, and strengthen password/risk controls before
broader public usage.

### SEC-10 — Fixed — proxy credentials are not plaintext Lambda configuration

The optional proxy input is stored as a KMS-encrypted SSM SecureString. The
yt-dlp function receives only the parameter name and gets the value with
decryption at runtime. Its role has exact-parameter ssm:GetParameter and
dedicated-key kms:Decrypt grants, and the handler avoids logging the proxy.

**Evidence:** IaC/ingestion.yaml, IaC/cloud-dsp.yaml, and LambdaYtDlp.py.

**Residual limitation:** the proxy must be plaintext in the Lambda process
while it is used. Code execution in that function, or a principal with the
same narrowly scoped SSM/KMS grants, can retrieve it. Provider-specific
credential rotation remains separate work.

### SEC-11 — Fixed — direct uploads are POST-only and size constrained

The browser accepts only the current presigned POST contract. S3 evaluates the
signed content-length-range before accepting the source, and Batch retains its
durable HeadObject size validation.

**Evidence:** ../../frontend/src/App.jsx (shared cloud profile) and job_api.py.

**Residual hardening:** the uploads bucket's CORS configuration still permits
PUT, but no active API/browser path supplies a signed PUT URL. Remove the
unused method when compatibility confirms it is unnecessary.

### SEC-12 — Low — the WebSocket handshake contains a full ID token in its query string

**Status: Open/accepted.** Native browser WebSockets cannot set arbitrary
opening-handshake headers, so the application uses an ID-token query
parameter. The authorizer validates issuer, audience, expiry, RS256, sub, and
token_use and does not log the token. It can nevertheless appear in proxy,
access-log, browser-diagnostic, or monitoring data until expiration.

**Remediation:** ensure log formats exclude query strings and authorization
material, keep WSS/token lifetimes short, and consider a short-lived,
single-use WebSocket ticket minted by the authenticated HTTP API.

### SEC-13 — Fixed — frontend environment files are no longer tracked

Root and frontend ignore rules exclude .env and .env.* while retaining
.env.example. The current tracked tree contains no frontend environment file.
The example contains only public browser configuration; Vite embeds every
VITE_* value into the browser bundle, so it must never contain a credential.

**Historical caveat:** Git-history review found expired presigned S3 URLs in
old mock/test content. They are recorded as SEC-19; this does not restore a
tracked .env issue.

### SEC-14 — Partially fixed — TLS is mandatory for IaC-managed S3 buckets

Explicit aws:SecureTransport=false deny statements now cover the uploads,
processed-audio, website, and demo-assets buckets. The private/versioned/
encrypted bucket controls remain in place.

**Evidence:** IaC/foundation.yaml and IaC/hosting.yaml.

**Remaining accepted risk:** repository-defined API Gateway access logging,
CloudTrail data events, WAF, and alerting are still absent. Live AWS CLI drift
verification could not run because the local session had expired.

### SEC-15 — Deferred — internal temporary-directory containment

LambdaYtDlp.py derives and clears /tmp/clouddsp-ytdlp/{job_id} before canonical
UUID validation and durable job lookup. The normal caller is the authenticated
Job API, which generates UUIDs, and the Lambda is not exposed as a public
HTTP/S3/EventBridge/WebSocket target. That keeps current risk low, but it
remains a future-integration boundary.

**Remediation:** parse the ID with uuid.UUID, use its canonical string for the
path, verify resolved-path containment before either cleanup, and remove or
explicitly gate the HTTP principalId compatibility fallback.

### SEC-16 — Accepted risk — public Batch hosts retain unrestricted HTTPS egress

Batch EC2 hosts launch in public subnets while running to avoid an always-on
NAT Gateway cost. They have no security-group ingress and retain the S3 gateway
endpoint, but TCP/443 may reach the public Internet through the Internet
Gateway. A compromised codec, dependency, or container could exfiltrate audio
or task-role credentials.

**Evidence:** IaC/network.yaml.

**Future remediation:** private Batch subnets with required VPC endpoints, or
a controlled/logged egress proxy, are the production alternative.

### SEC-17 — Archived — legacy handlers remain historical reference

The legacy presigned-URL generator, WebSocket notifier, and DSP plugin bypass
the durable job/ownership model or insufficiently validate input. A targeted
IaC search found no active reference to them. On 2026-10-03, they were moved to
`archive/dsp/cloud-prototypes/`, outside the active `src/DSP/cloud/` source and
the `src/DSP` container build context. Active handlers keep their owner-checked
job workflow and runtime entry points.

**Evidence:** [retired upload handler](../../archive/dsp/cloud-prototypes/presigned-upload/lambda-s3-presigned.py),
[callback notifier](../../archive/dsp/cloud-prototypes/websocket/WebSocketNotify.py),
and [effects prototype](../../archive/dsp/cloud-prototypes/effects/dsp_bitcrush_flanger_ringmod.py).

**Remediation completed:** archived the source and documented the active build
inputs in [the DSP guide](../src/DSP/README.md). The prototype code retains its
original behavior and must not be packaged as a current endpoint. This source
cleanup does not change existing deployed images or Lambda packages.

### SEC-18 — New — Medium — vulnerable PyJWT in the WebSocket authorizer enables JWKS-refresh amplification

requirements-websocket-authorizer.txt pins PyJWT with crypto extras at 2.10.1,
and the root stack defaults to a ZIP artifact that packages that version.
pip-audit reported the following advisories:

| CVE / advisory | Fixed version | CloudDSP assessment |
| --- | --- | --- |
| CVE-2026-32597 / GHSA-752w-5fwx-jx9f | 2.12.0 | Affected dependency; exploitation would require a valid Cognito signature with an unknown critical header. |
| CVE-2025-45768 | none; supplier disputes the finding | Not applicable to this RS256 Cognito verifier, which does not select a locally configured weak symmetric key. |
| CVE-2026-48522 / GHSA-993g-76c3-p5m4 | 2.13.0 | Mitigated by a fixed Cognito issuer URL rather than a JWT-supplied JKU URL. |
| CVE-2026-48523 / GHSA-jq35-7prp-9v3f | 2.12.1; upgrade to 2.13.0 | Mitigated: the code supplies signing_key.key and permits only RS256. |
| CVE-2026-48524 / GHSA-fhv5-28vv-h8m8 | 2.13.0 | **Reachable:** an unauthenticated connection caller controls kid; an unknown key ID can force a JWKS refresh and amplify an upstream failure. |
| CVE-2026-48525 / GHSA-w7vc-732c-9m39 | 2.13.0 | Detached-JWS parsing is not used by this authorizer. |
| CVE-2026-48526 / GHSA-xgmm-8j9v-c9wx | 2.13.0 | HMAC/raw-JWK path is not used; RS256 is explicitly restricted. |

The reachable case is an availability concern rather than an authentication
bypass: repeated invalid connection attempts can amplify requests to Cognito's
JWKS endpoint. There is no repository-defined connection throttle.

**Remediation:** pin PyJWT with crypto extras to 2.13.0 or a reviewed compatible
less-than-3 range, rebuild the authorizer ZIP for Lambda arm64 under a new
immutable artifact key, deploy it, and test both a valid Cognito connection and
malformed unknown-kid rejection. Add connection-attempt monitoring/limits
separately.

### SEC-19 — New — Low — expired S3 presigned URLs remain in Git history

Targeted current-tree scanning found no AWS access keys, private-key blocks,
GitHub/Slack tokens, or presigned URL credential parameters. A history scan
did find old mock/test diffs containing S3 presigned URLs with temporary STS
credentials. The most recent observed URL had already expired before this
reassessment, so it cannot currently be replayed.

**Impact:** if the repository was accessible while a URL remained valid, a
reader could have fetched its specific S3 object. A history rewrite does not
undo a prior download.

**Remediation:** add a CI secret-scanning rule for presigned URL credential
parameters, never commit generated links, and decide whether a coordinated
history rewrite is warranted based on repository exposure. If future active
credentials are committed, revoke/rotate them immediately rather than relying
on Git history changes.

## Verified controls

- Active Job API routes use API Gateway JWT authorization; job reads,
  subscriptions, and deletion enforce Cognito-sub ownership with opaque 404s.
- Browser ingestion uses short-lived exact-key, canonical-content-type,
  metadata-bearing presigned POST policies with a size range.
- Batch validates durable job/key state, source byte size, and FFprobe-readable
  audio duration before Demucs. Its subprocess invocation uses a fixed argv
  list, shell=False, and a timeout; Bandit raised no high-severity Python
  finding.
- MIDI workers validate a stem key against durable job state before output.
- S3 buckets are private, versioned, encrypted, bucket-owner-enforced, and
  CORS-scoped; DynamoDB uses encryption, TTL, and point-in-time recovery.
- The WebSocket authorizer validates issuer, audience, expiry, RS256, sub, and
  token_use; SEC-18 concerns how it obtains a signing key for invalid requests,
  not the normal validation rules.
- The demo catalog uses same-origin asset paths, pending signup storage
  contains only an email/display name, and the production bundle contains no
  source maps.

## Commands and limitations

| Check | Result |
| --- | --- |
| npm audit --omit=dev --json | 2 high production advisories (React Router pair) |
| npm audit --json | 4 high total advisories (adds PostCSS and Nanoid) |
| npm audit fix --dry-run --json | identified the patched versions in SEC-01; no dependency files changed |
| pip-audit against yt-dlp requirements | no known vulnerabilities |
| local DSP virtual environment | not a deployment artifact; its ignored setuptools copy has a developer-hygiene advisory and should be refreshed separately |
| pip-audit against WebSocket-authorizer requirements | PyJWT 2.10.1 advisories recorded in SEC-18 |
| Bandit cloud-source scan before archival | 0 high; temporary-path and fixed-argv subprocess warnings were reviewed |
| Docker Scout quickview | fresh local counts recorded for Basic Pitch, ADTOF, and yt-dlp; Demucs re-index did not complete |
| Production build and targeted frontend sink search | build succeeds; no source maps or active unsafe DOM/script sinks found |
| Targeted current-tree / Git-history secret review | current tree clear for targeted patterns; expired historical presigned URLs recorded in SEC-19 |
| Live website header request | CSP, HSTS, DENY anti-framing, nosniff, and referrer policy verified |
| Live AWS IAM/ECR/stack-drift review | not performed because the local AWS CLI session had expired |

The scan results above describe the original assessment. The 2026-10-03 source
reorganization moved active handlers to `src/DSP/cloud/` and retained prototypes
in `archive/dsp/`; it does not establish a new dependency or deployed-image scan.

Docker Scout was version 1.15.1 and reported an available CLI update. Scout
quickview counts are vulnerability inventory, not proof that every listed CVE
is reachable in CloudDSP code. Run ECR image scans against deployed digest
references after AWS reauthentication and gate releases on the resulting
triage.

## Remediation order

1. Upgrade PyJWT and rebuild/deploy the WebSocket-authorizer ZIP; then update
   the four JavaScript advisory paths in an isolated dependency change.
2. Triage/refresh worker images, make tags immutable, deploy by digest, and
   run Demucs as a non-root user.
3. Reduce deployment authority and use immutable/signed templates and ZIPs.
4. Add active-job/byte/rate abuse limits, route throttles, and operational
   monitoring.
5. Narrow worker S3 permissions and finish SSRF/egress containment.
6. Replace the query-string socket token with a short-lived ticket, remove
   dormant handlers, and add history-aware secret scanning.
