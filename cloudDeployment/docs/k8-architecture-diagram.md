# Local Kubernetes architecture diagram

The website's K8 page uses the shared
`../../frontend/public/architecture/cloud-dsp-k8-architecture.png` asset.
The illustration was generated with the built-in imagegen tool on 2026-10-02, then
reviewed against the versioned local deployment and corrected for connector accuracy.
The separate component ledger uses original project icons credited in
`../../frontend/public/architecture/k8-icons/README.md`.

The figure illustrates direct uploads and HTTP polling, CPU worker Deployments,
PostgreSQL authority, private MinIO artifacts, transactional outbox dispatch, and
RabbitMQ delivery that can duplicate work. Repeated service symbols refer to the
same running instance. It does not claim TLS, namespace-wide isolation, high
availability, CUDA support, or implemented realtime/linked-media services.

## Generation prompt

```text
Use case: infographic-diagram.
Asset type: final architecture diagram for the CloudDSP website's K8 page.
Primary request: a precise, polished landscape systems architecture diagram on a white background, similar to an AWS architecture reference diagram, but using recognizable Kubernetes and open-source product logos. Use crisp black typography, thin orthogonal directional arrows, generous spacing, flat vector-like rendering, no shadows or decorative illustration. Wide landscape around 2048 by 1280.

Title (verbatim): "CloudDSP on Local Kubernetes".
Boundary: thin rectangular outline labeled "k3d / K3s on Docker • 1 server + 2 CPU agents". Show a small blue Kubernetes helm-wheel logo and Docker whale beside this caption. This is a local CPU cluster, not a cloud or GPU deployment.

Arrange four clear horizontal bands with short readable labels. Brand icons should be specific: blue PostgreSQL elephant; orange RabbitMQ logo; red MinIO logo; cyan/blue Keycloak logo; blue React atom; Traefik proxy logo; Helm logo; KEDA logo. For application workers use simple distinct audio-waveform and MIDI-note/container icons, never AWS Lambda/Batch icons.

BAND 1 "BROWSER ACCESS": left CloudDSP Browser → Traefik → React Frontend. Connect React to Keycloak labeled "OIDC + PKCE". Keycloak → Mailpit labeled "registration email". React → Job API labeled "authenticated jobs + polling". Job API ↔ PostgreSQL labeled "owner + job state". Browser → MinIO labeled "presigned POST / GET". All private credentials stay server-side.

BAND 2 "DURABLE SOURCE HANDOFF": left MinIO "private uploads" → RabbitMQ "source events" → Upload Intake → PostgreSQL "task + outbox" → Dispatchers → RabbitMQ "durable work queues". Each arrow points left to right. PostgreSQL task creation and outbox insertion occur in one transaction.

BAND 3 "AUDIO + MIDI PROCESSING": RabbitMQ durable work queues → Demucs "stem separation"; Demucs → PostgreSQL "downstream outbox" → Dispatchers → RabbitMQ "MIDI queues"; MIDI queues branch to Basic Pitch "pitched stems" and ADTOF "drums". At the far right, both MIDI worker branches lead to MinIO "stems + MIDI + tempo" and PostgreSQL "durable results". Show all three workers visually as application containers. The Demucs result includes stored stems; downstream workers store MIDI/tempo. Connect KEDA via subtle dashed control arrows to the three worker nodes, labeled "scales worker Deployments".

BAND 4 "REPRODUCIBLE PLATFORM": Docker Hub → retained local registry → Helm releases. Short footer text (verbatim): "PostgreSQL is authoritative • RabbitMQ delivery is at least once • MinIO stores private artifacts". A second small note: "Repeated service symbols represent the same PostgreSQL, RabbitMQ and MinIO instances."

Namespace labels in a compact bottom legend, without implying network isolation: "clouddsp-app: UI, API, intake, dispatchers, workers" and "clouddsp-data: PostgreSQL, RabbitMQ, MinIO, Keycloak, Mailpit"; small note "KEDA in keda • Traefik in kube-system".

Constraints: All text correctly spelled and readable, no cropped elements, no false exactly-once guarantees, no WebSockets, no GPU, no AWS service icons, no encryption/TLS/HA claims, no passwords, no URLs with credentials. Clearly distinguish solid data arrows from dashed scaling arrows. Keep component names exact, especially "Keycloak", "PostgreSQL", "RabbitMQ", "MinIO", "Basic Pitch", "ADTOF".
```
## Connector correction prompt

```text
Use case: precise-object-edit.
Edit target: the supplied CloudDSP local Kubernetes architecture diagram.
Make a narrowly targeted connector correction only; preserve the entire layout, every component, all logos, background bands, readable text, title, colors, footers and namespace legend.

In band 3 at the far right, REMOVE the direct vertical connection between MinIO and PostgreSQL: these services do not talk directly to each other. Replace the worker output connectors with one simple junction from the Basic Pitch and ADTOF workers which branches into TWO independent arrows, one to MinIO for artifact writes, and one to PostgreSQL for durable result updates. No arrow should connect MinIO to PostgreSQL. Keep the outputs labeled exactly as now.

For the dashed blue KEDA control line, keep exactly THREE arrowheads, each ending at a worker container: Demucs, Basic Pitch and ADTOF. Remove the extra detached dashed arrowhead that points toward the branching MIDI queue connector. No KEDA control arrow should end at RabbitMQ or at a queue branch.

Everything else unchanged. White-background landscape architecture diagram, crisp typography and exact component labels. No new services or edges.
```
