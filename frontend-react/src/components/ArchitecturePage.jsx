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

const COMPONENTS = [
    {
        icon: null,
        group: 'Client',
        title: 'React processing workspace',
        text: 'The browser signs users in, creates a job, performs the constrained S3 upload, presents saved jobs, and hydrates audio and MIDI from fresh snapshots.',
        tags: ['React 19', 'Vite', 'Web Audio'],
    },
    {
        icon: 'cognito',
        group: 'Identity',
        title: 'Amazon Cognito User Pool',
        text: 'Issues the ID token used by the HTTP API JWT authorizer and WebSocket connect authorizer. Cognito sub is the permanent job owner; preferred_username is display-only.',
        tags: ['Authentication', 'JWT', 'User ownership'],
    },
    {
        icon: 'apiGateway',
        group: 'API edge',
        title: 'API Gateway HTTP & WebSocket APIs',
        text: 'The HTTP API exposes job creation, status snapshots, history, and terminal-job deletion. The WebSocket API carries low-latency subscription and update hints.',
        tags: ['JWT authorizer', 'CORS', 'Heartbeats'],
    },
    {
        icon: 'lambda',
        group: 'Serverless control',
        title: 'Job API and MIDI Lambdas',
        text: 'Job API produces presigned uploads/downloads and enforces account ownership. Basic Pitch extracts pitched MIDI while ADTOF produces the drum MIDI map.',
        tags: ['Python', 'Boto3', 'Container Lambdas'],
    },
    {
        icon: 'dynamoDb',
        group: 'Durable state',
        title: 'Amazon DynamoDB',
        text: 'CloudDSPJobs holds the job, state, stable S3 keys, artifacts, BPM, revision, and TTL. CloudDSPConnections holds short-lived WebSocket subscriptions only.',
        tags: ['On-demand', 'PITR', 'TTL + GSI'],
    },
    {
        icon: 's3',
        group: 'Private storage',
        title: 'Amazon S3',
        text: 'Separate versioned private buckets isolate original uploads from processed stems and MIDI. Browsers receive only short-lived presigned URLs, never AWS credentials.',
        tags: ['Versioned', 'Encrypted', 'Event source'],
    },
    {
        icon: 'eventBridge',
        group: 'Event routing',
        title: 'Amazon EventBridge',
        text: 'An Object Created event from the uploads bucket submits the Batch job. Metadata such as requested stem mode is deliberately read by Demucs from S3, not the transformer.',
        tags: ['Event-driven', 'Input transformer', 'DLQ'],
    },
    {
        icon: 'batch',
        group: 'GPU processing',
        title: 'AWS Batch · Demucs',
        text: 'Runs GPU stem separation in private VPC subnets. It validates source size/duration, persists ready stems, and directly invokes the appropriate MIDI extractor.',
        tags: ['GPU', 'Demucs', 'Private VPC'],
    },
    {
        icon: 'ecr',
        group: 'Image registry',
        title: 'Amazon ECR',
        text: 'Hosts the versioned Batch and Lambda images for Demucs, Basic Pitch, ADTOF, and yt-dlp. CloudFormation references images by repository and image tag.',
        tags: ['Demucs', 'MIDI', 'yt-dlp'],
    },
];

function ComponentsPanel() {
    return (
        <section className="architecture-components" aria-labelledby="architecture-components-heading">
            <div className="architecture-components-intro">
                <div>
                    <div className="architecture-section-kicker">COMPONENTS</div>
                    <h2 id="architecture-components-heading">A purpose-built service boundary for every stage</h2>
                </div>
                <p>CloudDSP separates state, event routing, GPU work, artifact delivery, and live presence so a slow worker or a disconnected browser never loses a completed result.</p>
            </div>
            <div className="architecture-component-grid">
                {COMPONENTS.map((component) => (
                    <article className="architecture-component-card" key={component.title}>
                        <div className="architecture-component-card-top">
                            {component.icon ? <AwsIcon icon={component.icon} label={component.title} /> : <span className="architecture-browser-icon architecture-browser-icon--small" aria-hidden="true">⌁</span>}
                            <span>{component.group}</span>
                        </div>
                        <h3>{component.title}</h3>
                        <p>{component.text}</p>
                        <div className="architecture-tags">
                            {component.tags.map((tag) => <span key={tag}>{tag}</span>)}
                        </div>
                    </article>
                ))}
            </div>
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
                    id="architecture-components-tab"
                    aria-selected={activeTab === 'components'}
                    aria-controls="architecture-components-panel"
                    className={activeTab === 'components' ? 'is-active' : ''}
                    onClick={() => setActiveTab('components')}
                >Components</button>
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
                id="architecture-components-panel"
                aria-labelledby="architecture-components-tab"
                hidden={activeTab !== 'components'}
            >
                <ComponentsPanel />
            </div>
        </main>
    );
}
