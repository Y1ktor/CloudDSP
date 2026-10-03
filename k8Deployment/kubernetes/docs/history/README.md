# Local Kubernetes implementation history

These documents preserve the earlier design, adoption inventories, development
steps, trial observations, and debugging evidence. They include superseded
commands, image tags, filenames, and statements that a later stage had not yet
been implemented. They are historical context, not current deployment runbooks.

Start with the [current documentation index](../../README.md),
[operator guide](../../scripts/README.md), or
[current architecture/status](../../../plan.md).

## Planning and ownership

| Historical record | Preserved context |
| --- | --- |
| [Incremental implementation log](2026-10-03-kubernetes-implementation-log.md) | Original long `k8Deployment/plan.md`, including requirements and stage-by-stage work. |
| [Resource ownership record](2026-10-03-resource-ownership-record.md) | Dated 2026-09-26 inventory and later adoption notes, original object counts and comparison catalog. |
| [Orchestration implementation notes](deployment-orchestration-implementation-notes.md) | Original dependency design, migration from raw resources to Helm, and partial-bootstrap milestones. |
| [Script reference before reorganization](scripts-reference-before-reorganization.md) | Original combined operator/helper guide and early foundation trials. |
| [Foundation command reference](foundation-command-reference.md) | Commands for the earlier routing-only cluster, before application/stateful deployment. |

## Service implementation notes

Current service READMEs describe the implemented interfaces and operating
boundaries. The full earlier development narratives remain here:

- [Job API](service-api-implementation-notes.md)
- [Upload intake](service-upload-intake-implementation-notes.md)
- [Dispatchers](service-dispatcher-implementation-notes.md)
- [Demucs](service-demucs-implementation-notes.md)
- [Basic Pitch](service-basic-pitch-implementation-notes.md)
- [ADTOF](service-adtof-implementation-notes.md)

## Trial and source evidence

[Dated VM trial records](../trials/) remain in their existing location. Each
records its tested source revision and conditions. Historical success does not
establish the state of another deployment; use the current verification gates
and the appropriate smoke contract.

[Frontend provenance](../../services/frontend/PROVENANCE.md) and
[ADTOF build provenance](../../images.lock.yaml) describe
where retained source came from. The unused [EQ prototypes](../../../../archive/eq/README.md)
have their own source archive. No user data, local populated Secret files, or
application backups are included in this documentation history.
