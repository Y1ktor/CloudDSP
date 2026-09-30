# Second Local VM Bootstrap Trial

## Result

The second `bootstrap-platform` run completed successfully in the existing
Multipass VM, `clouddsp-final-trial-9ed2746`, using source revision `9ed2746`.
All 91 bootstrap stages passed and the command exited with status 0.

The measured bootstrap interval was **19 minutes 2.633 seconds**:

- Start: `2026-09-30T02:36:25Z`
- End: `2026-09-30T02:55:28Z`
- Elapsed: `1,142.633 seconds`

The timer began immediately before invoking `deploy-local.sh bootstrap-platform`
and stopped when that command exited. It includes foundation preparation,
locked-image checks, cluster creation, and all application deployment stages.
It excludes starting the VM and the separate verification command.

## Verification

The subsequent read-only `deploy-local.sh verify` run passed all 56 configured
gates with exit status 0. At completion, the `clouddsp-local` k3d cluster had
one server and two agents; all three Kubernetes nodes were Ready, and 17 Helm
releases were installed.

Before deployment, all 19 locked CloudDSP image digests were present in the
retained local registry and matched the image lock. The bootstrap's local image
mirror stage verified these entries and reused them.

## Trial records

The VM stored the command output in
`/home/ubuntu/clouddsp-final-trial/bootstrap-platform-second.log`, the timing
record in `bootstrap-platform-second-timing.log`, and the verification output
in `verify-second.log`.
