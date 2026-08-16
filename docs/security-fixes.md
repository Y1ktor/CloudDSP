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
