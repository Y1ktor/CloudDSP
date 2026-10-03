import { useRef, useState } from 'react';
import './ArchitecturePage.css';
import './K8Page.css';

const repository = 'https://github.com/Y1ktor/CloudDSP';

function GitHubIcon() {
    return (
        <svg aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 19 19">
            <use href="/icons.svg#github-icon" />
        </svg>
    );
}

const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'workflow', label: 'Upload to MIDI' },
    { id: 'reliability', label: 'Reliable processing' },
    { id: 'security', label: 'Security' },
    { id: 'deployment', label: 'Local deployment' },
];

const services = [
    { icon: 'keycloak', name: 'Keycloak', role: 'Identity', detail: 'Signs users in through OpenID Connect and PKCE. Mailpit captures local account-verification and password-reset emails.' },
    { icon: 'postgresql', name: 'PostgreSQL', role: 'Durable state', detail: 'Owns jobs, users’ job ownership, task leases, retries, result keys, and the transactional outbox used to publish work.' },
    { icon: 'minio', name: 'MinIO', role: 'Object storage', detail: 'Stores private originals, stems, MIDI, and tempo results. A separate bucket exposes only shared instrument samples for browser playback.' },
    { icon: 'rabbitmq', name: 'RabbitMQ', role: 'Message delivery', detail: 'Carries source-upload events and stage-specific processing messages through durable queues, with bounded queues and dead-letter routes.' },
    { icon: 'keda', name: 'KEDA', role: 'Worker capacity', detail: 'Scales ordinary Kubernetes Deployments. Queue depth wakes workers; Demucs and Basic Pitch also observe durable PostgreSQL task counts.' },
    { icon: 'helm', name: 'Helm', role: 'Reproducible releases', detail: 'Owns the versioned service releases. The deployment orchestrator installs them in dependency order and verifies their external bootstrap state.' },
];

function Walkthrough({ steps, label }) {
    return (
        <ol className="ingestion-walkthrough k8-walkthrough" aria-label={label}>
            {steps.map(({ title, detail }, index) => (
                <li key={title}>
                    <span aria-hidden="true">{index + 1}</span>
                    <div><h3>{title}</h3><p>{detail}</p></div>
                </li>
            ))}
        </ol>
    );
}

function Overview() {
    return (
        <>
            <section className="architecture-diagram-shell" aria-labelledby="k8-diagram-heading">
                <div className="architecture-diagram-heading">
                    <div>
                        <div className="architecture-section-kicker">LOCAL KUBERNETES WORKFLOW</div>
                        <h2 id="k8-diagram-heading">Identity, durable state, queues, and audio workers</h2>
                    </div>
                    <a className="k8-text-link" href="/architecture/cloud-dsp-k8-architecture.png" target="_blank" rel="noreferrer">View full diagram <span aria-hidden="true">↗</span></a>
                </div>
                <figure className="ingestion-reference-diagram architecture-overview-figure">
                    <div className="architecture-diagram-image-scroll">
                        <img
                            src="/architecture/cloud-dsp-k8-architecture.png"
                            width="1586"
                            height="992"
                            alt="CloudDSP local Kubernetes workflow: browser access through Traefik, React, Keycloak and Job API; private MinIO uploads notify RabbitMQ; upload intake records PostgreSQL outbox work; dispatchers publish messages for Demucs, Basic Pitch and ADTOF; workers persist artifacts and results; KEDA scales their Deployments."
                            decoding="async"
                        />
                    </div>
                    <figcaption>
                        PostgreSQL is the authority for processing state. RabbitMQ transports work, and MinIO stores bytes. Repeated service symbols in the illustration refer to the same service; the browser reads durable results through Job API polling and receives temporary signed URLs for private artifacts.
                    </figcaption>
                </figure>
                <div className="architecture-flow-notes">
                    <p><b>Local topology.</b> One K3s server and two CPU agents run as Docker containers through k3d. They share one host; this is a development cluster.</p>
                    <p><b>Clear responsibilities.</b> Application pods live in <code>clouddsp-app</code>, data services in <code>clouddsp-data</code>, and support configuration in <code>clouddsp-system</code>.</p>
                    <p><b>Independent workers.</b> Demucs separates stems. Basic Pitch transcribes pitched stems, and ADTOF transcribes drums. Each stage persists its progress.</p>
                </div>
            </section>
            <section className="k8-service-section" aria-labelledby="k8-services-heading">
                <div className="architecture-section-kicker">COMPONENT RESPONSIBILITIES</div>
                <h2 id="k8-services-heading">Each service owns a distinct part of the workflow</h2>
                <div className="k8-service-grid">
                    {services.map(({ icon, name, role, detail }) => (
                        <article className="k8-service" key={name}>
                            <img src={`/architecture/k8-icons/${icon}.svg`} alt="" width="38" height="38" loading="lazy" />
                            <div><span>{role}</span><h3>{name}</h3><p>{detail}</p></div>
                        </article>
                    ))}
                </div>
                <p className="k8-source-note">Project symbols are stored locally from their credited icon sources. <a className="k8-github-link" href={`${repository}/tree/main/frontend/public/architecture/k8-icons`} target="_blank" rel="noreferrer"><GitHubIcon /> Icon sources and licenses on GitHub <span aria-hidden="true">↗</span></a></p>
            </section>
        </>
    );
}

function Workflow() {
    const steps = [
        { title: 'Sign in with Keycloak', detail: <>The React client uses Authorization Code with PKCE. Job API checks the access token’s signature, issuer, audience, and lifetime, then uses the immutable <code>sub</code> claim to identify the job owner.</> },
        { title: 'Create the job before moving audio', detail: <>An authenticated <code>POST /jobs</code> creates an <code>upload_pending</code> PostgreSQL row and reserves its object key. The API checks the media contract and returns a presigned POST with required metadata and a 256 MiB size ceiling.</> },
        { title: 'Upload directly to private object storage', detail: <>The browser submits the signed form to MinIO. An object-created notification for the <code>uploads/</code> prefix enters the RabbitMQ source-intake queue; the API does not proxy the audio bytes.</> },
        { title: 'Validate the source and record durable work', detail: <>Upload intake compares the event with the stored job and the object’s metadata. One PostgreSQL transaction records the accepted source and inserts the pending outbox event, so acceptance and the work to publish stay together.</> },
        { title: 'Publish confirmed stage messages', detail: <>The dispatchers lease pending outbox rows and publish persistent RabbitMQ messages with publisher confirms. Each stage has a reviewed route, and a row is marked delivered only after the broker confirms the publication.</> },
        { title: 'Separate validated audio into stems', detail: <>Demucs commits a durable task claim before acknowledging the message. It repeats the object-size check and validates the audio stream and 500-second duration limit with FFprobe before running the CPU model.</> },
        { title: 'Transcribe pitched stems and drums', detail: <>Demucs stores stems in MinIO and commits downstream outbox work. Basic Pitch handles pitched stems, and ADTOF handles drums. Workers store MIDI and tempo artifacts before committing the corresponding result keys and terminal task state.</> },
        { title: 'Recover the current result through polling', detail: <>The browser polls the owner-checked job endpoint while results are pending. Job API reads the authoritative PostgreSQL snapshot and creates fresh presigned GET URLs. Durable records contain object keys, so an expired URL does not lose the result.</> },
    ];
    return (
        <section className="architecture-components" aria-labelledby="k8-workflow-heading">
            <div className="architecture-section-kicker">DIRECT UPLOAD → STEMS → MIDI</div>
            <h2 id="k8-workflow-heading" className="k8-panel-heading">One durable path from a recording to editable music</h2>
            <p className="k8-intro">The local implementation currently supports direct file uploads and HTTP snapshots. Audio bytes move between the browser, workers, and MinIO; queue messages carry the identifiers needed to look up durable work.</p>
            <Walkthrough steps={steps} label="Local audio processing sequence" />
        </section>
    );
}

function Reliability() {
    return (
        <section className="architecture-components" aria-labelledby="k8-reliability-heading">
            <div className="architecture-section-kicker">AT-LEAST-ONCE PROCESSING</div>
            <h2 id="k8-reliability-heading" className="k8-panel-heading">A duplicate delivery must be safe to handle</h2>
            <p className="k8-intro">RabbitMQ can redeliver messages, and a dispatcher can crash after publishing but before recording success. The pipeline accepts that duplicate boundary and uses PostgreSQL task identity and guarded state transitions to preserve one logical result. Model execution can run again after a failure.</p>
            <Walkthrough label="Durable processing protections" steps={[
                { title: 'Keep the work beside its state change', detail: <>Task changes and their outbox events commit together. Dispatchers use row locks and lease tokens to claim work safely even when the Demucs-only and generic publishers are both running.</> },
                { title: 'Recognize duplicate logical tasks', detail: <>A unique <code>(job_id, stage, stem_name)</code> identifies processing work. A repeated notification or queue message checks that durable task instead of creating another independent job.</> },
                { title: 'Commit a claim before acknowledging', detail: <>Workers acknowledge RabbitMQ after the PostgreSQL claim commits, before inference. If the pod then exits, the expired database lease allows recovery. Lease tokens fence stale completions from a previous attempt.</> },
                { title: 'Bound execution and retries', detail: <>Model subprocesses have explicit deadlines, and durable task retries are bounded to three attempts. Invalid processing deliveries are rejected without requeue into dead-letter handling; exhausted attempts reach a terminal failure state.</> },
            ]} />
            <div className="k8-table-wrap" tabIndex="0" role="region" aria-label="Worker scaling and execution deadlines">
                <table className="k8-table">
                    <caption>KEDA scales worker Deployments within explicit local limits</caption>
                    <thead><tr><th scope="col">Worker</th><th scope="col">Replicas</th><th scope="col">Scaling signals</th><th scope="col">Model deadline</th></tr></thead>
                    <tbody>
                        <tr><th scope="row">Demucs</th><td>0–1</td><td>RabbitMQ + PostgreSQL task counts</td><td>12 minutes</td></tr>
                        <tr><th scope="row">Basic Pitch</th><td>0–3</td><td>RabbitMQ + PostgreSQL task counts</td><td>5 minutes</td></tr>
                        <tr><th scope="row">ADTOF</th><td>0–2</td><td>RabbitMQ queue depth</td><td>10 minutes</td></tr>
                    </tbody>
                </table>
            </div>
            <p className="k8-source-note">These are model execution limits, not guaranteed job turnaround times. Demucs and Basic Pitch retain a scaling signal for active or due database work after a message is acknowledged. ADTOF currently uses the broker signal alone.</p>
        </section>
    );
}

function Security() {
    return (
        <section className="architecture-components" aria-labelledby="k8-security-heading">
            <div className="architecture-section-kicker">IDENTITY AND LEAST PRIVILEGE</div>
            <h2 id="k8-security-heading" className="k8-panel-heading">Keep authority on the server and narrow each service’s access</h2>
            <p className="k8-intro">The browser is a public OIDC client. Database, storage, and broker credentials belong to server workloads; they are never bundled into React. Job access is based on the verified user subject.</p>
            <Walkthrough label="Implemented local security controls" steps={[
                { title: 'Verify identity at the API boundary', detail: <>PKCE protects the authorization-code exchange, with exact redirect URLs and no SPA client secret. API token validation and owner-filtered database reads prevent another user’s job identifier from authorizing an artifact read.</> },
                { title: 'Keep user artifacts private', detail: <>The uploads bucket includes source, stems, and MIDI under private prefixes. Browser transfer uses constrained signed forms and short-lived download URLs. Only the separate shared instrument-sample bucket permits anonymous object reads, with no list or write grant.</> },
                { title: 'Scope backend identities to their jobs', detail: <>Services use distinct PostgreSQL, MinIO, and RabbitMQ identities. Worker roles restrict database operations and artifact prefixes; dispatchers consume existing outbox work. KEDA has monitoring credentials rather than broker-administrator credentials.</> },
                { title: 'Limit pod privileges', detail: <>Normal service and worker pods run as non-root, drop Linux capabilities, disallow privilege escalation, and use RuntimeDefault seccomp with read-only root filesystems and bounded writable scratch space. Service-account token mounting is disabled; workers do not create Kubernetes Jobs.</> },
                { title: 'Generate private local credential files', detail: <>The initializer creates 33 ignored Secret sources with independent random credentials and supports a private override input. The directory is mode <code>700</code> and files are mode <code>600</code>. Values remain readable to the deployment owner, are not printed, and are not automatically replaced.</> },
                { title: 'Protect the broker’s network endpoints', detail: <>RabbitMQ’s ingress NetworkPolicy admits the reviewed application and monitoring pods to its listeners. PostgreSQL and the broker use internal services. Namespaces organize resources; full default-deny network isolation has not yet been installed across the cluster.</> },
            ]} />
            <aside className="k8-boundary-note" aria-labelledby="k8-boundaries-heading">
                <h3 id="k8-boundaries-heading">The current boundary is a trusted local machine</h3>
                <p>Application HTTP routes, the administrative API, and the unauthenticated local image registry bind to loopback. This profile has single-replica data services and local-path storage. TLS, high availability, backup/restore, and storage encryption are separate work; local file permissions and Kubernetes Secrets do not themselves encrypt values.</p>
                <p>Jobs carry a 14-day expiry and expired jobs are hidden by API reads. Physical object cleanup is not implemented by that timestamp. Cluster cleanup removes the local databases and objects.</p>
            </aside>
        </section>
    );
}

function Deployment() {
    return (
        <section className="architecture-components" aria-labelledby="k8-deployment-heading">
            <div className="architecture-section-kicker">VERSIONED DEPLOYMENT</div>
            <h2 id="k8-deployment-heading" className="k8-panel-heading">Build a fresh working cluster with one command</h2>
            <p className="k8-intro">The current profile uses Linux ARM64 images and CPU workers. After installing the prerequisites and configuring Docker’s local registry access, run this from the repository root:</p>
            <pre className="k8-command"><code>./k8Deployment/kubernetes/scripts/deploy-local.sh bootstrap-platform</code></pre>
            <Walkthrough label="Local deployment orchestration" steps={[
                { title: 'Guard the fresh-cluster boundary', detail: <>The command first checks that the fixed CloudDSP cluster is absent. It generates or validates the ignored credential sources before creating the foundation. A partial credential set stops the run.</> },
                { title: 'Fetch locked images from Docker Hub', detail: <>The scripts verify the reviewed public digests and mirror the 19 CloudDSP images into a loopback registry. The pinned K3s topology creates one server, two CPU agents, and the project namespaces.</> },
                { title: 'Install releases in dependency order', detail: <>The 93 stages bring up data services, bootstrap restricted identities and external state, configure Keycloak, apply schema migrations, install KEDA, and then install the frontend, API, intake, dispatchers, and workers through Helm.</> },
                { title: 'Verify state and clean up explicitly', detail: <>A separate <code>verify</code> command checks 56 gates. <code>cleanup</code> removes the cluster and its application data but retains the local registry and credential files. <code>purge-registry</code> separately removes the saved images after the cluster is gone.</> },
            ]} />
            <div className="k8-deployment-links">
                <a className="architecture-icons-credit k8-github-link" href={`${repository}#local`} target="_blank" rel="noreferrer"><GitHubIcon /> Deployment instructions on GitHub <span aria-hidden="true">↗</span></a>
                <a className="k8-text-link k8-github-link" href={`${repository}/blob/main/k8Deployment/kubernetes/scripts/README.md`} target="_blank" rel="noreferrer"><GitHubIcon /> Stages and smoke tests on GitHub <span aria-hidden="true">↗</span></a>
            </div>
            <p className="k8-source-note">The fresh automatic-credential path passed a disposable Ubuntu ARM64 VM trial: 93 deployment stages in 16m29s and all 56 verification checks. A separate VM trial verified custom credential input. Deployment creates new application state; it does not restore earlier data or validate CUDA performance.</p>
        </section>
    );
}

const panels = { overview: Overview, workflow: Workflow, reliability: Reliability, security: Security, deployment: Deployment };

export default function K8Page() {
    const [activeTab, setActiveTab] = useState('overview');
    const tabButtons = useRef([]);
    const ActivePanel = panels[activeTab];

    // Roving focus keeps the detail tabs navigable without adding each
    // inactive button to the keyboard's normal tab order.
    const moveTabFocus = (event, index) => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault();
        setActiveTab(tabs[next].id);
        tabButtons.current[next]?.focus();
    };

    return (
        <main className="architecture-page k8-page">
            <header className="architecture-hero">
                <div>
                    <div className="architecture-section-kicker">CLOUDDSP ON KUBERNETES</div>
                    <h1>A local cluster for durable audio processing.</h1>
                    <p>Helm-managed services bring identity, jobs, private object storage, and work queues together with CPU workers for stem separation and MIDI transcription.</p>
                </div>
                <a href={`${repository}#local`} target="_blank" rel="noreferrer" className="architecture-icons-credit k8-github-link"><GitHubIcon /> Deploy locally on GitHub <span aria-hidden="true">↗</span></a>
            </header>
            <div className="architecture-tabs" role="tablist" aria-label="Kubernetes architecture detail">
                {tabs.map(({ id, label }, index) => (
                    <button
                        type="button"
                        role="tab"
                        id={`k8-${id}-tab`}
                        key={id}
                        aria-selected={activeTab === id}
                        aria-controls={`k8-${id}-panel`}
                        tabIndex={activeTab === id ? 0 : -1}
                        ref={(button) => { tabButtons.current[index] = button; }}
                        className={activeTab === id ? 'is-active' : ''}
                        onClick={() => setActiveTab(id)}
                        onKeyDown={(event) => moveTabFocus(event, index)}
                    >{label}</button>
                ))}
            </div>
            {tabs.map(({ id }) => (
                <div key={id} role="tabpanel" id={`k8-${id}-panel`} aria-labelledby={`k8-${id}-tab`} hidden={activeTab !== id} tabIndex={0}>
                    {activeTab === id && <ActivePanel />}
                </div>
            ))}
        </main>
    );
}
