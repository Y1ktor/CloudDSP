import React, { useState } from 'react';
import './ArchitecturePage.css';

function OverviewDiagram() {
    return (
        <section className="architecture-diagram-shell" aria-labelledby="architecture-diagram-heading">
            <div className="architecture-diagram-heading">
                <div>
                    <div className="architecture-section-kicker">AWS WORKFLOW</div>
                    <h2 id="architecture-diagram-heading">Static demos and durable user jobs remain isolated</h2>
                </div>
            </div>

            <figure className="ingestion-reference-diagram architecture-overview-figure" aria-labelledby="overview-diagram-caption">
                <div className="architecture-diagram-image-scroll">
                    <img
                        src="/architecture/cloud-dsp-architecture-overview.png"
                        alt="CloudDSP AWS architecture overview separating static CloudFront website and anonymous demo delivery from authenticated Cognito, Job API, EventBridge, public-subnet Batch, MIDI Lambda, DynamoDB, WebSocket, and private S3 artifact workflows."
                    />
                </div>
                <figcaption id="overview-diagram-caption">
                    CloudFront serves the React build and curated examples from two dedicated private origins. Authenticated uploads, GPU processing, durable job state, realtime hints, and presigned artifact reads use a separate application path; CloudFront never proxies those APIs or private user artifacts.
                </figcaption>
            </figure>

            <div className="architecture-flow-notes">
                <p><b>1.</b> Anonymous examples stop at CloudFront and the demo-assets bucket; they never create backend jobs.</p>
                <p><b>2.</b> DynamoDB owns authenticated job state, while S3 owns source and generated artifact bytes.</p>
                <p><b>3.</b> WebSockets provide update hints; authenticated HTTP snapshots and fresh presigned URLs recover every result.</p>
            </div>
        </section>
    );
}

function IngestionPanel() {
    return (
        <section className="architecture-components architecture-ingestion" aria-label="Source ingestion workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="ingestion-diagram-caption">
                <div className="architecture-diagram-image-scroll">
                    <img
                        src="/architecture/secure-source-ingestion-linked-media.png"
                        alt="Secure Source Ingestion architecture diagram showing constrained multipart upload and allowlisted yt-dlp ingestion through Cognito, API Gateway, Job API Lambda, DynamoDB, encrypted proxy configuration, private uploads S3, and EventBridge."
                    />
                </div>
                <figcaption id="ingestion-diagram-caption">
                    A browser-selected file uses a size-constrained presigned POST, while an allowlisted media link is validated and normalized by yt-dlp. Both routes create an owner-bound job first, write to the same private <b>UPLOADS</b> bucket, and join one S3-to-EventBridge processing path.
                </figcaption>
            </figure>

            <ol className="ingestion-walkthrough" aria-label="Detailed source ingestion workflow">
                <li>
                    <span>1</span>
                    <div><h2>Authenticate the durable owner</h2><p>The user signs in with Amazon Cognito and the React client receives an ID token. APIs use its immutable <code>sub</code> claim for ownership; a display name, browser session, or WebSocket connection never becomes the job identity.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Request a direct upload</h2><p>For a local file, the browser calls <code>POST /jobs</code> through the API Gateway HTTP API. Its JWT authorizer verifies the Cognito token before Job API Lambda validates the filename, media type, exact byte count, requested stem mode, and source limits.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Persist the job and sign its contract</h2><p>Job API writes the owner-bound <code>upload_pending</code> record to DynamoDB before audio moves. It returns the <code>job_id</code>, reserved <code>uploads/{'{job_id}'}/…</code> key, signed form fields, and a presigned POST whose policy enforces the 256 MiB ceiling and required metadata.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Upload the original directly to S3</h2><p>The browser submits one multipart POST to the uploads bucket, passing every signed field unchanged with the file. S3 enforces the key, job metadata, content type, and 256 MiB content-length range; the API and Lambda never proxy or retain the source audio.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Create a linked-source job</h2><p>Alternatively, the browser calls <code>POST /jobs/link</code>. Job API repeats the HTTPS media-host allowlist check, creates a <code>source_ingestion</code> record for the same authenticated owner, and asynchronously invokes the yt-dlp image Lambda rather than submitting Batch directly.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Validate before downloading media</h2><p>The yt-dlp Lambda independently validates the HTTPS hostname and DNS result, retrieves media metadata, and enforces the 500-second duration and encoded-size limits. A progress hook caps unknown-size transfers, and the normalized WAV is checked again before upload.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Resolve the optional proxy securely</h2><p>When configured, yt-dlp reads a KMS-encrypted SSM SecureString with narrowly scoped decrypt permission; the credential is not placed in its environment. The worker uploads only the validated <code>linked-audio.wav</code> under the reserved job prefix with the same durable metadata as a browser upload.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Join one EventBridge handoff</h2><p>Either completed source creates the same S3 Object Created event. EventBridge forwards only the dynamic bucket and key into the Batch submission; the Demucs container later reads trusted S3 metadata with <code>HeadObject</code>. There is no second direct-to-Batch route.</p></div>
                </li>
            </ol>
        </section>
    );
}

function ProcessingPanel() {
    return (
        <section className="architecture-components architecture-processing" aria-label="Audio processing and MIDI extraction workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="processing-diagram-caption">
                <div className="architecture-diagram-image-scroll">
                    <img
                        src="/architecture/audio-processing-and-midi-extraction.png"
                        alt="Audio Processing and MIDI Extraction architecture diagram showing uploads S3, EventBridge retries and DLQ, GPU Demucs in public Batch subnets with Internet and S3 endpoint access, processed S3, Basic Pitch, ADTOF, and DynamoDB Jobs."
                    />
                </div>
                <figcaption id="processing-diagram-caption">
                    An object created in the <b>UPLOADS</b> bucket starts one retryable EventBridge-to-Batch workflow. GPU Demucs runs in ingress-free public Batch subnets, writes stems to the separate <b>PROCESSED</b> bucket, and then invokes CPU MIDI Lambdas that persist MIDI and BPM before updating durable job state.
                </figcaption>
            </figure>
            <ol className="ingestion-walkthrough" aria-label="Detailed audio processing and MIDI extraction workflow">
                <li>
                    <span>1</span>
                    <div><h2>Start with the completed S3 object</h2><p>When S3 accepts a source under <code>uploads/{'{job_id}'}/</code>, it emits an Object Created event. The uploads bucket is private and is only the durable source boundary; the browser is no longer part of this processing path.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Submit one retryable Batch job</h2><p>EventBridge transforms the S3 event into an AWS Batch submission using only <code>INPUT_BUCKET</code> and <code>FILE_KEY</code>. It retries a failed target delivery for the configured window and sends an exhausted submission to the SQS dead-letter queue for inspection or redrive.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Start GPU compute in public Batch subnets</h2><p>The managed compute environment pulls the Demucs image from Amazon ECR and launches EC2 GPU capacity in one of two public subnets. Instances receive public IPs and HTTPS egress through the Internet Gateway, accept no inbound traffic, and reach S3 through the VPC gateway endpoint.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Validate before expensive separation</h2><p><code>BatchDemucs.py</code> derives and verifies the canonical job identifier, reads object size and metadata with <code>HeadObject</code>, checks the requested stem mode against DynamoDB, and uses FFprobe to enforce the permitted duration and audio-stream boundary before loading Demucs.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Persist stems before downstream work</h2><p>Demucs writes every separated file beneath <code>stems/{'{job_id}'}/</code> in the private processed bucket, then records its stable key and queued MIDI state in DynamoDB. The configured 20-minute scale-down delay may keep the GPU host warm for a nearby job; setting it to zero restores immediate scale-down.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Invoke the correct MIDI extractor</h2><p>Batch asynchronously invokes Basic Pitch for vocals, bass, guitar, piano, and other pitched stems. Drums go to ADTOF, whose five-voice General MIDI mapping distinguishes kick, snare, tom, hi-hat, and cymbal. Both are CPU, x86_64 Lambda image workers outside the Batch VPC.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Write MIDI and tempo artifacts first</h2><p>Each extractor reads its immutable stem key and uploads <code>midi/{'{job_id}'}/*.mid</code> plus the relevant BPM JSON to the processed bucket. Artifact bytes must exist before a worker can advertise the corresponding durable key as ready.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Finalize durable state before notifying</h2><p>Workers persist stable keys, per-stem status, tempo candidates, master BPM, terminal state, and an incremented revision in DynamoDB Jobs. Only after that write succeeds do they emit <code>job_updated</code>; the delivery layer can therefore reconstruct the result even if that hint is duplicated or lost.</p></div>
                </li>
            </ol>
        </section>
    );
}

function DeliveryPanel() {
    return (
        <section className="architecture-components architecture-delivery" aria-label="Artifact delivery and live updates workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="delivery-diagram-caption">
                <div className="architecture-diagram-image-scroll">
                    <img
                        src="/architecture/artifact-delivery-and-live-updates.png"
                        alt="Durable Job Delivery and Live Updates architecture diagram showing Cognito-authenticated WebSocket hints, TTL subscriptions, durable HTTP polling and snapshots, DynamoDB job state, and direct original and processed artifact downloads from separate private S3 buckets."
                    />
                </div>
                <figcaption id="delivery-diagram-caption">
                    WebSocket messages contain only a job identifier and revision; they are low-latency hints, not result transport. The browser recovers through authenticated HTTP snapshots, then uses fresh presigned URLs to read the original from <b>UPLOADS</b> and stems, MIDI, and BPM from <b>PROCESSED</b>.
                </figcaption>
            </figure>
            <ol className="ingestion-walkthrough" aria-label="Detailed artifact delivery and live updates workflow">
                <li>
                    <span>1</span>
                    <div><h2>Authenticate the WebSocket connection</h2><p>The browser opens the API Gateway WebSocket with a short-lived Cognito ID token in its <code>token</code> query parameter. The WebSocket authorizer verifies that token before accepting the connection; browser WebSockets cannot set the normal HTTP <code>Authorization</code> opening header.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Authorize and manage the socket lifecycle</h2><p>The WebSocket authorizer verifies the token before API Gateway accepts <code>$connect</code>. A separate handler processes <code>subscribe</code>, two-minute heartbeats, and <code>$disconnect</code>; heartbeats keep the socket active and detect failure but never retrieve artifacts.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Verify ownership and store a temporary subscription</h2><p>On <code>subscribe</code>, the handler checks DynamoDB Jobs to confirm that the Cognito <code>sub</code> owns the selected job. It writes the short-lived connection and job subscription to DynamoDB Connections, where TTL and disconnect cleanup keep realtime state disposable.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Persist first, then send a lightweight hint</h2><p>Processing workers write S3 keys, state, tempo, and a new revision to DynamoDB Jobs before querying active connections. They send only <code>{'{job_id, revision}'}</code> through API Gateway WebSocket. The hint may be late, duplicated, or missed without losing a result.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Refresh through authenticated HTTP</h2><p>The browser calls <code>GET /jobs/{'{job_id}'}</code> after a hint, on reconnect, and every five seconds while expected artifacts remain pending. Its retry backoff protects the API after 429 or transient 5xx responses; a dropped socket therefore cannot strand the workspace.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Build the owner-checked snapshot</h2><p>The HTTP API JWT authorizer and Job API Lambda use the immutable Cognito <code>sub</code> to enforce ownership. Job API reads the current DynamoDB record, including revision, source status, master tempo, and stable keys for original audio, stems, MIDI, and BPM artifacts.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Sign fresh artifact reads</h2><p>Job API generates one-hour presigned GET URLs only while assembling the response. DynamoDB never stores those disposable signatures; it retains stable bucket keys so history reads and polling snapshots can always issue a fresh URL.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Download directly from both private buckets</h2><p>The browser reads the original from the uploads bucket and reads stems, MIDI, and BPM from the processed bucket, bypassing API Gateway and Lambda for large transfers. Its caches use stable host-plus-path identity so refreshed query signatures do not trigger duplicate decoding.</p></div>
                </li>
            </ol>
        </section>
    );
}

function HostingDemoPanel() {
    return (
        <section className="architecture-components architecture-hosting" aria-label="Web hosting and anonymous demo delivery workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="hosting-diagram-caption">
                <div className="architecture-diagram-image-scroll">
                    <img
                        src="/architecture/web-hosting-and-demo-delivery.png"
                        alt="Web Hosting and Anonymous Demo Delivery architecture diagram showing Route 53 and ACM, a CloudFront distribution and Function, response security headers, dedicated OAC access to private website and demo-assets S3 origins, and a browser-local anonymous demo workspace."
                    />
                </div>
                <figcaption id="hosting-diagram-caption">
                    A release publishes the Vite build and curated examples independently, then invalidates one CloudFront distribution. Its default behavior reads the private website origin; only <code>/demo/*</code> reaches the separate demo-assets origin. Neither behavior proxies application APIs or private user artifacts.
                </figcaption>
            </figure>

            <ol className="ingestion-walkthrough" aria-label="Detailed web hosting and anonymous demo delivery workflow">
                <li>
                    <span>1</span>
                    <div><h2>Resolve the canonical site at the edge</h2><p>Route 53 A and AAAA aliases for the apex and <code>www</code> names resolve to the CloudFront distribution. The S3 origins have no public website endpoints and are never exposed through public bucket policies.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Terminate modern HTTPS with ACM</h2><p>An ACM certificate created in <code>us-east-1</code> covers both names and is validated through the supplied Route 53 zone. CloudFront enforces TLS 1.2 or newer and redirects ordinary HTTP viewers to HTTPS.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Canonicalize and route known React pages</h2><p>A viewer-request CloudFront Function redirects <code>www</code> to the apex while preserving the path and query string. It rewrites only known client routes such as <code>/architecture</code>, <code>/cost</code>, and <code>/stems</code> to <code>index.html</code>, avoiding a global SPA fallback.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Serve the application from private website S3</h2><p>The default cache behavior signs origin requests through the website-specific Origin Access Control. The versioned bucket blocks public access and permits <code>GetObject</code> only when the request comes from this CloudFront distribution.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Route demos to a separate private origin</h2><p>The <code>demo/*</code> cache behavior selects the demo-assets bucket and a second dedicated OAC. Curated originals, stems, MIDI, BPM files, and <code>demo/manifest.json</code> remain independent of website synchronization and the 14-day lifecycle used for user jobs.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Apply browser security headers</h2><p>The response-headers policy adds the reviewed Content Security Policy, HSTS, frame denial, MIME-sniffing protection, and referrer policy. An optional WAF Web ACL can attach to the same distribution without changing origin access.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Validate the same-origin demo catalog</h2><p>The React app requests <code>/demo/manifest.json</code>, accepts only JSON, and rejects artifact URLs outside the same-origin <code>/demo/*</code> namespace. Missing manifests or media remain genuine 403/404 responses rather than being rewritten to application HTML.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Hydrate a browser-local example</h2><p>The selected manifest entry becomes a completed <code>demo:&lt;id&gt;</code> workspace. Playback, MIDI editing, and downloads run locally; no shared Cognito identity, Job API request, WebSocket subscription, DynamoDB item, Batch job, Lambda worker, or presigned URL is involved.</p></div>
                </li>
            </ol>
        </section>
    );
}

export default function ArchitecturePage() {
    const [activeTab, setActiveTab] = useState('overview');

    return (
        <main className="architecture-page">
            <header className="architecture-hero">
                <div>
                    <div className="architecture-section-kicker">CLOUDDSP ON AWS</div>
                    <h1>Architecture that separates public exploration from durable audio work.</h1>
                    <p>CloudDSP serves curated examples at the edge, while authenticated uploads become stems and editable MIDI through an isolated, event-driven AWS workflow.</p>
                </div>
                <a href="https://aws.amazon.com/architecture/icons/" target="_blank" rel="noreferrer" className="architecture-icons-credit">
                    AWS Architecture Icons <span aria-hidden="true">↗</span>
                </a>
            </header>

            <div className="architecture-tabs" role="tablist" aria-label="Architecture detail">
                <button
                    type="button"
                    role="tab"
                    id="architecture-overview-tab"
                    aria-selected={activeTab === 'overview'}
                    aria-controls="architecture-overview-panel"
                    className={activeTab === 'overview' ? 'is-active' : ''}
                    onClick={() => setActiveTab('overview')}
                >Overview</button>
                <button
                    type="button"
                    role="tab"
                    id="architecture-ingestion-tab"
                    aria-selected={activeTab === 'ingestion'}
                    aria-controls="architecture-ingestion-panel"
                    className={activeTab === 'ingestion' ? 'is-active' : ''}
                    onClick={() => setActiveTab('ingestion')}
                >Ingestion</button>
                <button
                    type="button"
                    role="tab"
                    id="architecture-processing-tab"
                    aria-selected={activeTab === 'processing'}
                    aria-controls="architecture-processing-panel"
                    className={activeTab === 'processing' ? 'is-active' : ''}
                    onClick={() => setActiveTab('processing')}
                >Processing</button>
                <button
                    type="button"
                    role="tab"
                    id="architecture-delivery-tab"
                    aria-selected={activeTab === 'delivery'}
                    aria-controls="architecture-delivery-panel"
                    className={activeTab === 'delivery' ? 'is-active' : ''}
                    onClick={() => setActiveTab('delivery')}
                >Delivery</button>
                <button
                    type="button"
                    role="tab"
                    id="architecture-hosting-tab"
                    aria-selected={activeTab === 'hosting'}
                    aria-controls="architecture-hosting-panel"
                    className={activeTab === 'hosting' ? 'is-active' : ''}
                    onClick={() => setActiveTab('hosting')}
                >Web hosting &amp; demo delivery</button>
            </div>

            <div
                role="tabpanel"
                id="architecture-overview-panel"
                aria-labelledby="architecture-overview-tab"
                hidden={activeTab !== 'overview'}
            >
                <OverviewDiagram />
            </div>
            <div
                role="tabpanel"
                id="architecture-ingestion-panel"
                aria-labelledby="architecture-ingestion-tab"
                hidden={activeTab !== 'ingestion'}
            >
                <IngestionPanel />
            </div>
            <div
                role="tabpanel"
                id="architecture-processing-panel"
                aria-labelledby="architecture-processing-tab"
                hidden={activeTab !== 'processing'}
            >
                <ProcessingPanel />
            </div>
            <div
                role="tabpanel"
                id="architecture-delivery-panel"
                aria-labelledby="architecture-delivery-tab"
                hidden={activeTab !== 'delivery'}
            >
                <DeliveryPanel />
            </div>
            <div
                role="tabpanel"
                id="architecture-hosting-panel"
                aria-labelledby="architecture-hosting-tab"
                hidden={activeTab !== 'hosting'}
            >
                <HostingDemoPanel />
            </div>
        </main>
    );
}
