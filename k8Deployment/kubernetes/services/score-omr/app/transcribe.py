"""Bounded CPU homr inference and MusicXML-to-MIDI conversion."""

from copy import deepcopy
from pathlib import Path
import re
import os
import signal
import subprocess
import sys
import time

import mido
from music21 import converter, stream
from PIL import Image, UnidentifiedImageError

MAX_PAGES = 4
MAX_PIXELS = 36_000_000
PAGE_SECONDS = 120


class InvalidScore(ValueError):
    """The uploaded score cannot safely yield playable notation."""


class InferenceTimeout(RuntimeError):
    """A model or PDF renderer exceeded its CPU deadline."""


def _run(args: list[str], *, seconds: int, tick):
    # A separate process group lets the deadline stop the renderer/model and
    # any descendants. The broker heartbeat remains in the parent process.
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    deadline = time.monotonic() + seconds
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise InferenceTimeout("Score processing timed out.")
            tick()
            time.sleep(0.5)
        if process.returncode != 0:
            raise InvalidScore("Score could not be recognized.")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def _pages(source: Path, content_type: str, work: Path, tick) -> list[Path]:
    if content_type == "application/pdf":
        if source.read_bytes()[:5] != b"%PDF-":
            raise InvalidScore("The uploaded PDF is invalid.")
        try:
            info = subprocess.run(["pdfinfo", str(source)], capture_output=True,
                                  text=True, timeout=15, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            raise InvalidScore("The uploaded PDF is invalid.") from error
        match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
        if match is None or not 1 <= int(match.group(1)) <= MAX_PAGES:
            raise InvalidScore(f"PDF scores must have 1–{MAX_PAGES} pages.")
        prefix = work / "page"
        _run(["pdftoppm", "-f", "1", "-l", match.group(1), "-scale-to", "3000",
              "-png", str(source), str(prefix)], seconds=60, tick=tick)
        pages = sorted(work.glob("page-*.png"))
        if len(pages) != int(match.group(1)):
            raise InvalidScore("The PDF pages could not be rendered.")
        return pages
    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_PIXELS:
                raise InvalidScore("The score image is too large.")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise InvalidScore("The uploaded score image is invalid.") from error
    return [source]


def transcribe(source: Path, content_type: str, work: Path, tick) -> tuple[Path, Path]:
    pages = _pages(source, content_type, work, tick)
    xml_pages = []
    for page in pages:
        _run([sys.executable, "-m", "app.homr_cpu", str(page)],
             seconds=PAGE_SECONDS, tick=tick)
        xml = page.with_suffix(".musicxml")
        if not xml.is_file() or xml.stat().st_size == 0:
            raise InvalidScore("No readable music was found on a score page.")
        xml_pages.append(xml)

    try:
        scores = [converter.parse(str(path)) for path in xml_pages]
        if not all(score.parts for score in scores):
            raise InvalidScore("No readable musical parts were found.")
        if len({len(score.parts) for score in scores}) != 1:
            raise InvalidScore("The score pages have incompatible parts.")
        combined = deepcopy(scores[0])
        for next_page in scores[1:]:
            for target, extra in zip(combined.parts, next_page.parts):
                for measure in extra.getElementsByClass(stream.Measure):
                    target.append(deepcopy(measure))
        midi = work / "result.mid"
        xml = work / "result.musicxml"
        combined.write("musicxml", fp=str(xml))
        combined.write("midi", fp=str(midi))
        notes = sum(message.type == "note_on" and message.velocity > 0
                    for track in mido.MidiFile(str(midi)).tracks for message in track)
        if notes == 0:
            raise InvalidScore("No playable notes were recognized.")
        return midi, xml
    except InvalidScore:
        raise
    except Exception as error:
        raise InvalidScore("The recognized notation could not become MIDI.") from error
