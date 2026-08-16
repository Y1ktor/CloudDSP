import React, { useState } from 'react';
import './ArchitecturePage.css';

// Official AWS Architecture Icons, Q2 2026. The source package is published
// by AWS at https://aws.amazon.com/architecture/icons/.
const ICONS = {
    apiGateway: '/aws-icons/amazon-api-gateway.svg',
    batch: '/aws-icons/aws-batch.svg',
    cognito: '/aws-icons/amazon-cognito.svg',
    dynamoDb: '/aws-icons/amazon-dynamodb.svg',
    ecr: '/aws-icons/amazon-ecr.svg',
    eventBridge: '/aws-icons/amazon-eventbridge.svg',
    lambda: '/aws-icons/aws-lambda.svg',
    s3: '/aws-icons/amazon-s3.svg',
};

function AwsIcon({ icon, label }) {
    return <img className="architecture-service-icon" src={ICONS[icon]} alt={`${label} AWS architecture icon`} />;
}

function DiagramNode({ icon, eyebrow, title, detail, accent = 'default' }) {
    return (
        <article className={`architecture-node architecture-node--${accent}`}>
            {icon ? <AwsIcon icon={icon} label={title} /> : <span className="architecture-browser-icon" aria-hidden="true">⌁</span>}
            <div>
                <div className="architecture-node-eyebrow">{eyebrow}</div>
                <div className="architecture-node-title">{title}</div>
                {detail && <div className="architecture-node-detail">{detail}</div>}
            </div>
        </article>
    );
}

function DiagramArrow({ label, dashed = false }) {
    return (
        <div className={`architecture-arrow${dashed ? ' architecture-arrow--dashed' : ''}`} aria-hidden="true">
            {label && <span>{label}</span>}
            <i>→</i>
        </div>
    );
}

function OverviewDiagram() {
    return (
        <section className="architecture-diagram-shell" aria-labelledby="architecture-diagram-heading">
            <div className="architecture-diagram-heading">
                <div>
                    <div className="architecture-section-kicker">AWS WORKFLOW</div>
                    <h2 id="architecture-diagram-heading">One durable job, from source to editable MIDI</h2>
                </div>
                <div className="architecture-legend" aria-label="Diagram legend">
                    <span><b className="architecture-legend-line" /> durable request or artifact flow</span>
                    <span><b className="architecture-legend-line architecture-legend-line--dashed" /> notification or readback</span>
                </div>
            </div>

            <div className="architecture-diagram-scroll">
                <div className="architecture-diagram">
                    <div className="architecture-lane-label">Identity &amp; control</div>
                    <div className="architecture-lane architecture-lane--control">
                        <DiagramNode eyebrow="WEB CLIENT" title="CloudDSP React app" detail="Upload, history, DAW" accent="client" />
                        <DiagramArrow label="ID token" />
                        <DiagramNode icon="cognito" eyebrow="AUTHENTICATION" title="Amazon Cognito" detail="User Pool" />
                        <DiagramArrow label="JWT" />
                        <DiagramNode icon="apiGateway" eyebrow="HTTP API" title="API Gateway" detail="JWT authorizer" />
                        <DiagramArrow label="routes" />
                        <DiagramNode icon="lambda" eyebrow="JOB API" title="Job API Lambda" detail="Create, read, delete" />
                        <DiagramArrow label="state" />
                        <DiagramNode icon="dynamoDb" eyebrow="DURABLE STATE" title="DynamoDB Jobs" detail="Owner, status, S3 keys" />
                    </div>

                    <div className="architecture-lane-label">Event-driven processing</div>
                    <div className="architecture-lane architecture-lane--processing">
                        <DiagramNode icon="s3" eyebrow="PRIVATE SOURCE" title="Uploads bucket" detail="uploads/{job_id}/…" />
                        <DiagramArrow label="Object Created" />
                        <DiagramNode icon="eventBridge" eyebrow="EVENT ROUTING" title="Amazon EventBridge" detail="Input transformer" />
                        <DiagramArrow label="submit job" />
                        <DiagramNode icon="batch" eyebrow="GPU COMPUTE" title="AWS Batch · Demucs" detail="Split stems" />
                        <DiagramArrow label="stems" />
                        <DiagramNode icon="s3" eyebrow="PRIVATE ARTIFACTS" title="Processed bucket" detail="stems/{job_id}/…" />
                        <DiagramArrow label="invoke" />
                        <DiagramNode icon="lambda" eyebrow="MIDI WORKERS" title="Basic Pitch & ADTOF" detail="Pitched + drums MIDI" />
                    </div>

                    <div className="architecture-lane-label">Artifacts &amp; live updates</div>
                    <div className="architecture-lane architecture-lane--delivery">
                        <DiagramNode icon="s3" eyebrow="MIDI ARTIFACTS" title="Processed bucket" detail="midi/{job_id}/…" />
                        <DiagramArrow label="persist status" />
                        <DiagramNode icon="dynamoDb" eyebrow="AUTHORITATIVE RESULT" title="DynamoDB Jobs" detail="Keys, BPM, revisions" />
                        <DiagramArrow label="job_updated" dashed />
                        <DiagramNode icon="apiGateway" eyebrow="WEBSOCKET API" title="API Gateway WebSocket" detail="Subscribe + heartbeat" />
                        <DiagramArrow label="snapshot hint" dashed />
                        <DiagramNode eyebrow="BROWSER" title="CloudDSP React app" detail="Fresh presigned URLs" accent="client" />
                    </div>
                </div>
            </div>

            <div className="architecture-flow-notes">
                <p><b>1.</b> The Job API owns the job record before the source is uploaded.</p>
                <p><b>2.</b> Batch and MIDI workers store S3 keys and state before publishing a notification.</p>
                <p><b>3.</b> The browser reads a fresh job snapshot; a WebSocket is never the source of truth.</p>
            </div>
        </section>
    );
}

function ComponentsPanel() {
    return (
        <section className="architecture-components architecture-ingestion" aria-label="Source ingestion workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="ingestion-diagram-caption">
                <img
                    src="/architecture/secure-source-ingestion-linked-media.png"
                    alt="Secure Source Ingestion architecture diagram showing direct file upload and alternate yt-dlp linked-media ingestion through Cognito, API Gateway, Job API Lambda, DynamoDB, S3, and EventBridge."
                />
                <figcaption id="ingestion-diagram-caption">
                    Steps 1–5 cover a browser-selected audio file. Steps 6–8 show the alternate linked-media route; both paths write to the same uploads bucket and share the same S3-to-EventBridge processing handoff.
                </figcaption>
            </figure>

            <ol className="ingestion-walkthrough" aria-label="Detailed source ingestion workflow">
                <li>
                    <span>1</span>
                    <div><h2>Authentication</h2><p>The user signs in with Amazon Cognito. The React client receives a Cognito ID token, which identifies the immutable user <code>sub</code>; that identity is used for job ownership rather than a display name or browser session.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Create the job through the API</h2><p>The browser calls <code>POST /jobs</code> with its ID token and <code>filename</code>, <code>content_type</code>, <code>size_bytes</code>, and <code>stem_mode</code>. API Gateway verifies the JWT, then Job API Lambda validates the request and writes an <code>upload_pending</code> record to DynamoDB before any audio bytes move.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Return the signed upload contract</h2><p>Job API returns <code>201 Created</code> with the durable <code>job_id</code>, reserved <code>input_key</code>, <code>upload_url</code>, signed <code>upload_fields</code>, and <code>max_source_bytes</code>. This short-lived presigned POST contract is the only authority the browser receives for the upload.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Upload the original directly to S3</h2><p>The browser submits one multipart POST to the uploads bucket, passing every signed field unchanged with the file. S3 enforces the key, job metadata, content type, and 256 MiB content-length range; the API and Lambda never proxy or retain the source audio.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Hand off processing through EventBridge</h2><p>Either completed upload produces the same S3 Object Created event. EventBridge passes the dynamic bucket and key to the Batch submission; the Demucs worker later uses <code>HeadObject</code> to read durable metadata such as the requested stem mode before it begins processing.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Request linked-media ingestion</h2><p>Instead of selecting a local file, the browser calls <code>POST /jobs/link</code> through the same authenticated API Gateway. Job API creates the same owner-bound job, records the supplied public media URL, and marks it as awaiting source ingestion.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Invoke the yt-dlp worker</h2><p>Job API asynchronously invokes the yt-dlp Lambda. It validates the public URL, checks available metadata and configured limits, downloads the source, and normalizes its audio to the permitted WAV format before S3 receives any object.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Write the normalized linked source</h2><p>yt-dlp uploads the validated result to <code>uploads/{'{job_id}'}/linked-audio.wav</code> with the same <code>job-id</code> and <code>stem-mode</code> metadata as a browser upload. This joins step 5 rather than creating a second Batch path.</p></div>
                </li>
            </ol>
        </section>
    );
}

function ProcessingPanel() {
    return (
        <section className="architecture-components architecture-processing" aria-label="Audio processing and MIDI extraction workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="processing-diagram-caption">
                <img
                    src="/architecture/audio-processing-and-midi-extraction.png"
                    alt="Audio Processing and MIDI Extraction architecture diagram showing the uploads S3 bucket, EventBridge, private VPC GPU Demucs Batch job, processed S3 bucket, Basic Pitch, ADTOF, and DynamoDB Jobs."
                />
                <figcaption id="processing-diagram-caption">
                    An object created in the <b>UPLOADS</b> bucket starts the event-driven processing workflow. Demucs writes stems to the separate <b>PROCESSED</b> bucket, then Basic Pitch and ADTOF create the durable MIDI and BPM artifacts used by the application.
                </figcaption>
            </figure>
            <ol className="ingestion-walkthrough" aria-label="Detailed audio processing and MIDI extraction workflow">
                <li>
                    <span>1</span>
                    <div><h2>Start with the completed S3 object</h2><p>When S3 accepts a source under <code>uploads/{'{job_id}'}/</code>, it emits an Object Created event. The uploads bucket is private and is only the durable source boundary; the browser is no longer part of this processing path.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Route the event through EventBridge</h2><p>EventBridge transforms the S3 event into an AWS Batch submission using only the dynamic bucket and object key. Object metadata is deliberately not copied into the event, which keeps the routing rule independent of user-controlled metadata.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Validate before GPU separation</h2><p>The private-VPC Batch job runs GPU Demucs. Before it starts expensive separation, <code>BatchDemucs.py</code> uses <code>HeadObject</code> to enforce source limits and retrieve the job metadata, then uses FFprobe to verify a permitted audio stream and duration.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Persist the stems in the processed bucket</h2><p>Demucs writes each separated stem beneath <code>stems/{'{job_id}'}/</code> in the distinct processed bucket. Persisting the files first makes their keys durable inputs for the downstream Lambdas and allows later job snapshots to create fresh download URLs.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Send pitched stems to Basic Pitch</h2><p>For vocals, bass, guitar, piano, and other pitched stems, the Batch worker directly invokes Basic Pitch with the processed S3 stem key. The Lambda downloads the identified stem and extracts standard note MIDI without requiring GPU compute.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Send the drum stem to ADTOF</h2><p>The drums stem follows a separate direct invocation to ADTOF. Its CPU model identifies drum events such as kick, snare, tom, hi-hat, and cymbal, preserving the General MIDI drum-note mapping needed by the browser’s drum subtracks.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Store MIDI and tempo artifacts</h2><p>Basic Pitch and ADTOF upload MIDI under <code>midi/{'{job_id}'}/</code> in the processed bucket. The workflow also stores the accompanying BPM artifact so the durable result includes the information required to determine the track tempo.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Persist the job result before delivery</h2><p>Workers write stable artifact keys, extraction state, BPM data, and a new revision to DynamoDB before sending a <code>job_updated</code> notification. The next delivery component turns that durable snapshot into fresh presigned URLs; a notification is only a prompt to read it.</p></div>
                </li>
            </ol>
        </section>
    );
}

function DeliveryPanel() {
    return (
        <section className="architecture-components architecture-delivery" aria-label="Artifact delivery and live updates workflow">
            <figure className="ingestion-reference-diagram" aria-labelledby="delivery-diagram-caption">
                <img
                    src="/architecture/artifact-delivery-and-live-updates.png"
                    alt="Artifact Delivery and Live Updates architecture diagram showing browser WebSocket subscriptions and heartbeats, durable HTTP job snapshots, DynamoDB state, and direct downloads from the processed S3 bucket."
                />
                <figcaption id="delivery-diagram-caption">
                    WebSocket messages are low-latency update hints, not result transport. After an update, the browser calls the authenticated job API for a durable snapshot and receives fresh presigned URLs for direct reads from the <b>PROCESSED</b> bucket.
                </figcaption>
            </figure>
            <ol className="ingestion-walkthrough" aria-label="Detailed artifact delivery and live updates workflow">
                <li>
                    <span>1</span>
                    <div><h2>Authenticate the WebSocket connection</h2><p>The browser opens the API Gateway WebSocket with a short-lived Cognito ID token in its <code>token</code> query parameter. The WebSocket authorizer verifies that token before accepting the connection; browser WebSockets cannot set the normal HTTP <code>Authorization</code> opening header.</p></div>
                </li>
                <li>
                    <span>2</span>
                    <div><h2>Subscribe to one owned job</h2><p>After connecting, the browser sends <code>subscribe</code> for the job open in the workspace and sends a heartbeat every two minutes. The handler verifies that the Cognito <code>sub</code> owns the job, then stores a short-lived connection and subscription record in DynamoDB Connections.</p></div>
                </li>
                <li>
                    <span>3</span>
                    <div><h2>Persist the result before any notification</h2><p>Batch and MIDI workers first write stable S3 keys, extraction state, BPM data, and a new job revision to DynamoDB Jobs. This is the authoritative record; completed artifacts are never represented only by an in-memory worker response or WebSocket message.</p></div>
                </li>
                <li>
                    <span>4</span>
                    <div><h2>Send a lightweight update hint</h2><p>Once the durable job record is updated, the worker sends <code>job_updated</code> through the WebSocket API. Messages may be late, duplicated, or missed, so the browser treats this only as a prompt to refresh—not as an artifact payload or a correctness boundary.</p></div>
                </li>
                <li>
                    <span>5</span>
                    <div><h2>Read the durable job snapshot</h2><p>The browser calls <code>GET /jobs/{'{job_id}'}</code> with its Cognito ID token after a notification, on reconnect, and while expected artifacts are still pending. Polling provides a fallback if a connection expires or a WebSocket update never arrives.</p></div>
                </li>
                <li>
                    <span>6</span>
                    <div><h2>Verify ownership and load stable keys</h2><p>API Gateway’s JWT authorizer and Job API Lambda use the immutable Cognito <code>sub</code> to enforce ownership. Job API reads the DynamoDB record, including its current revision and the durable S3 keys for original audio, stems, MIDI, and BPM artifacts.</p></div>
                </li>
                <li>
                    <span>7</span>
                    <div><h2>Generate fresh, short-lived download URLs</h2><p>Job API generates presigned S3 GET URLs only while assembling this response. URLs are deliberately not stored in DynamoDB because they expire; stable S3 keys remain the authoritative artifact identity.</p></div>
                </li>
                <li>
                    <span>8</span>
                    <div><h2>Download artifacts directly from S3</h2><p>The browser fetches the returned URLs directly from the processed bucket, bypassing API Gateway and Lambda for large audio and MIDI transfers. It keys its local audio and MIDI work by stable S3 path rather than each disposable presigned query string.</p></div>
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
                    <h1>Architecture that keeps long-running audio work dependable.</h1>
                    <p>CloudDSP turns an uploaded or linked track into stems and editable MIDI through a durable, event-driven AWS workflow.</p>
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
                    aria-selected={activeTab === 'components'}
                    aria-controls="architecture-ingestion-panel"
                    className={activeTab === 'components' ? 'is-active' : ''}
                    onClick={() => setActiveTab('components')}
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
                hidden={activeTab !== 'components'}
            >
                <ComponentsPanel />
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
        </main>
    );
}
