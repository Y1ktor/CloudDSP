"""Compare a local reference benchmark with the real worker pipeline.

The caller mounts their PDF and benchmark directory read-only. No copyrighted
score/outputs, credentials or user storage coordinates are embedded in tests.
Run inside the worker image with PYTHONPATH=/app and this file mounted at /tests.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import mido
from PIL import Image

from app.transcribe import transcribe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    midi, xml = transcribe(args.pdf, "application/pdf", args.output, lambda: None)
    reference = args.benchmark / "midi/cpu/debussy-combined.mid"
    pages = sorted(args.output.glob("page-*.musicxml"))
    page_matches = [page.read_bytes() == (args.benchmark / "cpu" / f"debussy-{n}.musicxml").read_bytes()
                    for n, page in enumerate(pages, 1)]
    result = {
        "wall_seconds": round(time.monotonic() - started, 2),
        "rendered_dimensions": [Image.open(p.with_suffix(".png")).size for p in pages],
        "page_xml_matches": page_matches,
        "midi_bytes_match": midi.read_bytes() == reference.read_bytes(),
        "midi_sha256": hashlib.sha256(midi.read_bytes()).hexdigest(),
        "note_ons": sum(m.type == "note_on" and m.velocity > 0 for t in mido.MidiFile(midi).tracks for m in t),
        "duration_seconds": mido.MidiFile(midi).length,
        "combined_xml_pitched_notes": len(ET.parse(xml).findall(".//note/pitch")),
    }
    (args.output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if len(pages) != 2 or not all(page_matches) or not result["midi_bytes_match"]:
        raise SystemExit("Debussy output differs from the reference benchmark.")


if __name__ == "__main__":
    main()
