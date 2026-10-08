"""Regression checks for bounded PDF rendering and page timing preservation."""

from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from music21 import meter, note, stream
from PIL import Image

from app.transcribe import InvalidScore, _combine_scores, _pages, _write_results


class TranscriptionTests(unittest.TestCase):
    def test_fixed_dpi_and_pixel_limit_are_both_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "source.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            info = "Pages: 1\nPage 1 MediaBox: 0.00 0.00 648.00 888.00\n"
            def render(args, **_kwargs):
                self.assertEqual(args[args.index("-r") + 1], "300")
                self.assertNotIn("-scale-to", args)
                Image.new("RGB", (10, 10), "white").save(work / "page-1.png")
            with patch("app.transcribe.subprocess.run", return_value=CompletedProcess([], 0, stdout=info)), \
                    patch("app.transcribe._run", side_effect=render):
                self.assertEqual(_pages(source, "application/pdf", work, lambda: None), [work / "page-1.png"])
            oversized = "Pages: 1\nPage 1 MediaBox: 0 0 7200 7200\n"
            with patch("app.transcribe.subprocess.run", return_value=CompletedProcess([], 0, stdout=oversized)), \
                    patch("app.transcribe._run") as renderer:
                with self.assertRaises(InvalidScore):
                    _pages(source, "application/pdf", work, lambda: None)
                renderer.assert_not_called()

    def page(self, lengths):
        score = stream.Score()
        for length in lengths:
            part = stream.Part()
            measure = stream.Measure(number=1)
            measure.append(note.Note("C4", quarterLength=length))
            part.insert(0, measure)
            score.insert(0, part)
        return score

    def test_page_boundary_uses_one_offset_for_unequal_staff_durations(self):
        first, second, third = self.page([4, 1]), self.page([2, 3]), self.page([1, 1])
        combined = _combine_scores([first, second, third])
        self.assertEqual([[m.offset for m in p.getElementsByClass(stream.Measure)]
                          for p in combined.parts], [[0, 4, 7], [0, 4, 7]])
        # Inference output remains reusable: assembling/exporting must not edit
        # the parsed page or its part/measure ownership and relative offsets.
        self.assertEqual(len(first.parts[0].getElementsByClass(stream.Measure)), 1)
        self.assertEqual(second.parts[0].getElementsByClass(stream.Measure)[0].offset, 0)

    def test_incompatible_pages_are_rejected_before_export(self):
        with self.assertRaises(InvalidScore):
            _combine_scores([self.page([4, 4]), self.page([4])])

    def test_sparse_voice_ids_export_without_notation_repair(self):
        score = stream.Score()
        part = stream.Part()
        first = stream.Measure(number=1)
        first.insert(0, meter.TimeSignature("3/4"))
        upper = stream.Voice(id="1")
        upper.append(note.Rest(quarterLength=3))
        lower = stream.Voice(id="7")
        # Imperfect OMR can leave an overfull voice and no matching voice in
        # the next measure. makeNotation tries to tie into absent voice '7'.
        lower.append(note.Note("C4", quarterLength=4))
        first.insert(0, upper)
        first.insert(0, lower)
        second = stream.Measure(number=2)
        voice = stream.Voice(id="1")
        voice.append(note.Note("D4", quarterLength=3))
        second.insert(0, voice)
        part.insert(0, first)
        part.insert(3, second)
        score.insert(0, part)
        with tempfile.TemporaryDirectory() as directory:
            midi, xml = _write_results(score, Path(directory))
            self.assertTrue(midi.read_bytes().startswith(b"MThd"))
            self.assertEqual(len(ET.parse(xml).findall(".//note/pitch")), 2)
        self.assertEqual([m.offset for m in part.getElementsByClass(stream.Measure)], [0, 3])


if __name__ == "__main__":
    unittest.main()
