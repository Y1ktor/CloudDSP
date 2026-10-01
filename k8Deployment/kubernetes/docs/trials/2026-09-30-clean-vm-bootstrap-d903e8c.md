# Full Clean VM Bootstrap Trial

## Result

The committed `d903e8c` source completed `deploy-local.sh bootstrap-platform`
successfully in a new Multipass VM. All 91 stages passed and the command exited
with status 0.

The bootstrap took **17 minutes 11.20 seconds**:

- Started: `2026-09-30 22:18:32 ADT`
- Finished: `2026-09-30 22:35:43 ADT`
- Elapsed: `1,031.20 seconds`

The timer covered the bootstrap command, including fresh foundation creation,
anonymous Docker Hub source verification, mirroring all 19 locked ARM64 images,
and all application stages. It excludes VM creation and preparation.

## Environment and verification

The VM was Ubuntu 24.04.5 ARM64 with 8 CPUs, 16 GiB of RAM, and a 100 GiB
disk. It began without a CloudDSP cluster or local registry. After bootstrap,
all three k3d nodes were Ready and 17 Helm releases were installed. The separate
read-only `deploy-local.sh verify` passed all 56 configured gates.

The ignored credential files from `k8Deployment/.local/` were transferred
directly into the VM and were not included in the committed source archive.
The VM was deleted after verification. This trial validates deployment and
the configured verification gates; it did not run the end-to-end upload and
audio-processing smoke workflow.
