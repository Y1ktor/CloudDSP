"""Bounded CPU homr inference and MusicXML-to-MIDI conversion."""

from copy import deepcopy
from pathlib import Path
import math
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
PDF_DPI = 300


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
            info = subprocess.run(["pdfinfo", "-box", "-f", "1", "-l", str(MAX_PAGES), str(source)], capture_output=True,
                                  text=True, timeout=15, check=True)
        except (subprocess.SubprocessError, OSError) as error:
            raise InvalidScore("The uploaded PDF is invalid.") from error
        match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
        if match is None or not 1 <= int(match.group(1)) <= MAX_PAGES:
            raise InvalidScore(f"PDF scores must have 1–{MAX_PAGES} pages.")
        # Fixed DPI matches the evaluated Mac inputs. Inspect each MediaBox
        # first: removing the old longest-edge cap must not allow huge PDF
        # canvases to exhaust the Pod before the decoded-image limit applies.
        boxes = re.findall(
            r"^Page\s+\d+\s+MediaBox:\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$",
            info.stdout, re.MULTILINE,
        )
        if len(boxes) != int(match.group(1)):
            raise InvalidScore("The PDF page sizes could not be read.")
        try:
            for box in boxes:
                x1, y1, x2, y2 = map(float, box)
                width = math.ceil((x2 - x1) * PDF_DPI / 72)
                height = math.ceil((y2 - y1) * PDF_DPI / 72)
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise InvalidScore("A PDF score page is too large at 300 DPI.")
        except InvalidScore:
            raise
        except (ValueError, OverflowError) as error:
            raise InvalidScore("The PDF page sizes are invalid.") from error
        prefix = work / "page"
        _run(["pdftoppm", "-f", "1", "-l", match.group(1), "-r", str(PDF_DPI),
              "-png", str(source), str(prefix)], seconds=60, tick=tick)
        pages = sorted(work.glob("page-*.png"))
        if len(pages) != int(match.group(1)):
            raise InvalidScore("The PDF pages could not be rendered.")
        for page in pages:
            _validate_image(page)
        return pages
    _validate_image(source)
    return [source]


def _validate_image(source: Path) -> None:
    """Apply the same decoded-pixel boundary to uploads and rendered pages."""

    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_PIXELS:
                raise InvalidScore("The score image is too large.")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise InvalidScore("The uploaded score image is invalid.") from error


def _combine_scores(scores):
    """Keep the benchmark's page offsets and simultaneous staff alignment.

    A short pickup or incomplete staff must not move the next page earlier
    for that part. Use one offset from the whole page and preserve each
    measure's relative offset instead of appending to each part's duration.
    """

    if not all(score.parts for score in scores):
        raise InvalidScore("No readable musical parts were found.")
    if len({len(score.parts) for score in scores}) != 1:
        raise InvalidScore("The score pages have incompatible parts.")
    combined = stream.Score()
    for part in scores[0].parts:
        combined.insert(0, deepcopy(part))
    page_offset = scores[0].duration.quarterLength
    for next_page in scores[1:]:
        for target, extra in zip(combined.parts, next_page.parts):
            for measure in extra.getElementsByClass(stream.Measure):
                target.insert(page_offset + measure.offset, deepcopy(measure))
        page_offset += next_page.duration.quarterLength
    return combined


def _write_results(combined, work: Path) -> tuple[Path, Path]:
    midi = work / "result.mid"
    xml = work / "result.musicxml"
    # MIDI must use the recognized timing directly, as in the benchmark.
    # MusicXML's automatic makeNotation pass can split polyphonic measures
    # and crashes on Debussy's sparse voice IDs. Preserve the imported
    # notation on a separate copy so either exporter cannot alter playback.
    combined.write("midi", fp=str(midi))
    deepcopy(combined).write("musicxml", fp=str(xml), makeNotation=False)
    notes = sum(message.type == "note_on" and message.velocity > 0
                for track in mido.MidiFile(str(midi)).tracks for message in track)
    if notes == 0:
        raise InvalidScore("No playable notes were recognized.")
    return midi, xml


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
        return _write_results(_combine_scores(scores), work)
    except InvalidScore:
        raise
    except Exception as error:
        raise InvalidScore("The recognized notation could not become MIDI.") from error
