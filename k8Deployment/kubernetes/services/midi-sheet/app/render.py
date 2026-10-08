"""Bounded, offline MuseScore engraving of a validated Standard MIDI File."""
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import mido

class InvalidScore(ValueError):
    """A safe, owner-visible invalid MIDI/export failure."""


def validate_midi(source):
    if source.stat().st_size > 10 * 1024 * 1024 or source.read_bytes()[:4] != b'MThd':
        raise InvalidScore('Choose a valid Standard MIDI File up to 10 MiB.')
    try:
        midi = mido.MidiFile(source)
        if midi.type not in (0, 1) or not 1 <= midi.ticks_per_beat <= 32767:
            raise ValueError('unsupported MIDI timing')
        if len(midi.tracks) > 64 or sum(len(t) for t in midi.tracks) > 100000:
            raise ValueError('too many MIDI events')
        duration = midi.length
        notes = sum(m.type == 'note_on' and m.velocity > 0 for t in midi.tracks for m in t)
        if not 1 <= notes <= 20000 or not 0 < duration <= 1800:
            raise ValueError('MIDI duration/note limit')
    except Exception as error:
        raise InvalidScore('MIDI must contain notes, use metrical timing, and be at most 30 minutes (64 tracks, 20,000 notes).') from error


def render(source: Path, _content_type: str, work: Path, *, tick=lambda: None):
    validate_midi(source)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp/runtime'))
    runtime.mkdir(mode=0o700, exist_ok=True)
    pdf, xml = work / 'result.pdf', work / 'result.musicxml'
    recipe = work / 'job.json'
    recipe.write_text(json.dumps([{'in': str(source), 'out': [str(pdf), str(xml)]}]))
    # The official Linux binary selects xcb, so Xvfb supplies an isolated
    # virtual display with TCP disabled. It needs neither a desktop nor a GPU.
    # AppRun configures the bundle's Qt/library paths. A process deadline sits
    # inside the database lease and Pod termination grace period.
    with (work / 'renderer.log').open('wb') as log:
        process = subprocess.Popen(['xvfb-run', '-a', '-s', '-screen 0 1280x1024x24 -nolisten tcp', '/opt/squashfs-root/AppRun', '-j', str(recipe)], stdout=log, stderr=log,
                                   start_new_session=True)
        deadline = time.monotonic() + 120
        try:
            while process.poll() is None:
                tick()
                if time.monotonic() >= deadline:
                    raise InvalidScore('This MIDI exceeded the sheet rendering time limit.')
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    if process.returncode != 0:
        raise InvalidScore('MuseScore could not render this MIDI. Check the file and try again.')
    try:
        if not pdf.exists() or not pdf.read_bytes().startswith(b'%PDF-'):
            raise ValueError('missing PDF')
        if pdf.stat().st_size > 50 * 1024 * 1024 or xml.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('oversize output')
        if ET.parse(xml).getroot().tag != 'score-partwise':
            raise ValueError('invalid MusicXML')
    except Exception as error:
        raise InvalidScore('The sheet renderer did not produce valid PDF and MusicXML files.') from error
    return pdf, xml
