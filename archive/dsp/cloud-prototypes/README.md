# Archived cloud DSP prototypes

These historical files were moved out of deployable source on **2026-10-03**.
They are excluded from the current AWS and Kubernetes deployment paths. The
[active AWS implementation](../../../cloudDeployment/src/DSP/) owns the
current authenticated Job API, durable job workflow, image recipes, and
WebSocket handlers. Do not include this archive in Lambda packages or active
image build contexts.

The prototype Python sources retain their original behavior. Archiving them
does not add owner checks, input validation, upload limits, or a deployment
contract; use them as historical reference rather than application handlers.

## Original locations

Paths in this table are relative to the repository root before the move.

| Archived file | Original location |
| --- | --- |
| [Presigned upload Lambda](presigned-upload/lambda-s3-presigned.py) | `cloudDeployment/src/DSP/src/Cloud/presigned_url/lambda-s3-presigned.py` |
| [Connection-ID echo Lambda](websocket/EchoConnectionId.py) | `cloudDeployment/src/DSP/src/Cloud/webSocketAPI/EchoConnectionId.py` |
| [Batch result notifier](websocket/WebSocketNotify.py) | `cloudDeployment/src/DSP/src/Cloud/webSocketAPI/WebSocketNotify.py` |
| [SQS effects Lambda](effects/dsp_bitcrush_flanger_ringmod.py) | `cloudDeployment/src/DSP/src/Cloud/plugin/dsp_bitcrush_flanger_ringmod.py` |
| [Madmom Lambda](madmom/LambdaMIDIMadmom.py) | `cloudDeployment/src/DSP/src/Cloud/LambdaMIDIMadmom.py` |
| [Madmom image recipe](madmom/Dockerfile) | `cloudDeployment/src/DSP/docker/madmom/Dockerfile.lambda` |
| [Madmom workflow snapshot](madmom/cloud_job_workflow.py) | Copied from `cloudDeployment/src/DSP/src/Cloud/cloud_job_workflow.py` on the archive date. The active helper remains separate. |

## Retired upload and callback design

The presigned-upload prototype returns an S3 **PUT** URL for
`uploads/local/{uuid}-{filename}` and puts a browser connection ID into object
metadata. The echo Lambda gives the browser that temporary connection ID.
The result notifier later takes the connection ID from a Batch environment,
lists stems using the source filename, and pushes presigned result URLs
directly to that connection.

The current deployment creates an authenticated durable job first, uses a
size-constrained presigned **POST**, stores stable artifact keys, and sends
job-update hints to authenticated subscribers. The browser retrieves an
owner-checked snapshot and recovers through polling. These archived callback
and upload handlers are not part of that workflow.

The effects handler is an unprovisioned SQS/Pedalboard experiment with its own
input/output keys. It has no active CloudFormation or frontend route.

## Optional Madmom experiment

Madmom was not provisioned by the current CloudFormation MIDI stack or selected
by the Batch dispatcher. Its retained handler already uses durable job IDs and
the workflow helper; it does **not** use the retired connection-ID callback
design described above.

The adjacent `cloud_job_workflow.py` is a byte-preserved snapshot copied when
Madmom was archived. It keeps this historical experiment independent of later
changes to active deployment source. The retained Dockerfile uses **this
`madmom/` directory** as its complete build context, copies only those adjacent
Python inputs, and preserves `Madmom.lambda_handler` plus the original runtime
and dependency recipe. This archive is not a supported deployment target.
