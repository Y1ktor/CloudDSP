# Archived DSP experiments and cloud prototypes

These earlier implementations were separated from active deployment source on
2026-10-03. They remain in Git for reference. The active AWS handlers and image
recipes are documented in [the cloud DSP guide](../../cloudDeployment/src/DSP/README.md).
The local Kubernetes backend is [a separate deployment](../../k8Deployment/plan.md).

```text
dsp/
  experiments/            # Earlier standalone Demucs, Basic Pitch, MIR, notes CLIs
  manual-checks/          # Standalone Pedalboard examples; no automated assertions
  assets/                 # Original sample audio and prototype event fixtures
  output/                 # Ignored generated stems, MIDI, and analysis
  cloud-prototypes/
    basic-pitch-obsolete/ # Historical broken TensorFlow/Batch Docker recipe
    madmom/               # Unprovisioned Lambda extractor and workflow snapshot
    websocket/            # Earlier connection-ID callback handlers
    presigned-upload/     # Retired presigned-PUT upload handler
    effects/              # Unprovisioned SQS effects handler
```

## Host experiments

The old `cloudDeployment/src/DSP/src/Local/` scripts are now
[standalone experiments](experiments/README.md). `Local` described host Python
experiments, not the Kubernetes implementation. Their asset/output defaults now
resolve under this archive from the script's location, including when launched
from another working directory. Explicit command-line paths remain available.

The three `manual-checks/test_*.py` programs preserve the earlier bitcrush,
flanger, and ring-modulator examples. They read `assets/PYT-sample.wav`, write
ignored `test_out_*.wav` files beside themselves, and require NumPy/Pedalboard.
They are manual examples rather than unit tests of the active processing path.
Existing generated cloud output stays at its original ignored location;
archiving source does not migrate previous analysis results.

## Cloud prototypes

[The prototype guide](cloud-prototypes/README.md) records original locations and
dependencies. The archived upload/callback/effects handlers do not implement the
current authentication and durable job ownership boundaries. They are excluded
from active image contexts and Lambda source; do not package them as current
CloudDSP endpoints.

Madmom is an unprovisioned alternative extractor. Its retained handler uses
durable job IDs and has a snapshot of its workflow dependency. The deployed drum
extractor remains ADTOF. Existing CloudFormation resources, including the Madmom
ECR repository, retain their original lifecycle.

`basic-pitch-obsolete/Dockerfile` preserves the old recipe, with comment whitespace normalized. It refers
to `CloudExtractBasicPitch.py`, a source file absent from the repository, and
is historical evidence rather than a supported build. The active Lambda recipe
is now `cloudDeployment/src/DSP/docker/basic_pitch/Dockerfile`.
