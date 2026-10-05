"""One reviewed CPU ADTOF configuration shared by plans and process commands.

These source-level constants preserve the current cloud worker's default ADTOF
revision and inference settings. They are deliberately not environment values:
an environment override would make a stored artifact's provenance claim one
configuration while the model ran another. A reviewed future change must update
this file, tests, output provenance, and the worker image together.
"""

from __future__ import annotations


ADTOF_MODEL_SOURCE_REVISION = "85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9"
ADTOF_CPU_DEVICE = "cpu"
ADTOF_FPS = 100
ADTOF_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)

# Keep the CLI text stable rather than formatting floats at runtime. This is
# the exact five-value order used by the cloud worker: kick, snare, tom,
# hi-hat, and cymbal.
ADTOF_THRESHOLDS_ARGUMENT = "0.22,0.24,0.32,0.22,0.30"
ADTOF_MODEL_CONFIGURATION_ID = (
    f"adtof-pytorch@{ADTOF_MODEL_SOURCE_REVISION}"
    f";fps={ADTOF_FPS};thresholds={ADTOF_THRESHOLDS_ARGUMENT};device={ADTOF_CPU_DEVICE}"
)
