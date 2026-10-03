# Retained local DSP experiments

These standalone scripts were moved from
`cloudDeployment/src/DSP/src/Local/`. They remain available for manual research
and are outside both deployment pipelines. Their sibling input assets were moved
from `cloudDeployment/src/DSP/assets/` to [`../assets/`](../assets/).

Each script resolves its defaults from its own location, so it can be launched
from any working directory. Generated files belong in the ignored
`archive/dsp/output/` directory.

| Script | Default input under `archive/dsp/` | Default output under `archive/dsp/` | Dependencies |
| --- | --- | --- | --- |
| [StemSplit.py](StemSplit.py) | `assets/PYT-sample.wav` | `output/stems/` | Demucs |
| [ExtractMIR.py](ExtractMIR.py) | `output/stems/htdemucs_6s/Yosemite/guitar.wav` | `output/analysis/guitar_data.json` | Librosa, NumPy, SciPy |
| [ProcessNotes.py](ProcessNotes.py) | `output/analysis/guitar_data.json` | `output/analysis/guitar_notes.json` | Librosa |
| [ExtractBasicPitch.py](ExtractBasicPitch.py) | `output/stems/htdemucs_6s/Yosemite/piano.wav` | `output/analysis/` | Basic Pitch |

The original defaults are preserved, including the Yosemite stem paths. These
are examples, rather than a sequential pipeline: splitting the default
`PYT-sample.wav` produces a `PYT-sample` stem directory. Supply explicit paths to
process another sample or connect these stages. For example, from the repository
root, with the required dependencies installed:

```bash
python archive/dsp/experiments/StemSplit.py \
  --input archive/dsp/assets/Yosemite.mp3 \
  --output archive/dsp/output/stems \
  --mode 6-stems
python archive/dsp/experiments/ExtractMIR.py \
  --input archive/dsp/output/stems/htdemucs_6s/Yosemite/guitar.wav \
  --output archive/dsp/output/analysis/guitar_data.json \
  --type guitar
```

The three Pedalboard checks moved from `cloudDeployment/src/DSP/tests/` to
[`../manual-checks/`](../manual-checks/). They are manually launched audio
experiments, rather than automated assertion tests. Each uses
`../assets/PYT-sample.wav` relative to its own directory and writes an ignored
`test_out_*.wav` beside the script. Bitcrush and ring modulation also require
NumPy.
