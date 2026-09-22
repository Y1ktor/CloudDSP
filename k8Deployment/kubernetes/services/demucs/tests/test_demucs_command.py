"""Unit tests for the fixed local-CPU Demucs command builder.

These tests create only temporary empty files/directories. They do not execute
the returned command, load Demucs/Torch, contact MinIO/PostgreSQL/RabbitMQ, or
interact with Docker or Kubernetes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.demucs_command import (
    DEMUCS_CPU_CLI_SCRIPT,
    DEMUCS_DEVICE,
    DEMUCS_MODEL_REPOSITORY,
    DEMUCS_PYTHON_EXECUTABLE,
    DemucsCommandContractError,
    DemucsCommandPathError,
    build_demucs_separation_command,
)


class DemucsCommandTests(unittest.TestCase):
    """Prove only fixed model arguments and private fresh scratch paths pass."""

    def test_each_supported_stem_mode_builds_its_exact_cpu_command(self) -> None:
        """The Kubernetes builder preserves the cloud model-selection contract."""

        expected = {
            "2-stems": ("htdemucs", ("--two-stems", "vocals")),
            "4-stems": ("htdemucs", ()),
            "6-stems": ("htdemucs_6s", ()),
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_directory = Path(temporary_directory) / "scratch"
            work_directory.mkdir()
            source_path = work_directory / "input" / "source.media"
            source_path.parent.mkdir()
            source_path.write_bytes(b"validity-is-owned-by-earlier-preflight")

            for stem_mode, (model_name, mode_arguments) in expected.items():
                with self.subTest(stem_mode=stem_mode):
                    output_directory = work_directory / f"output-{stem_mode}"
                    output_directory.mkdir()

                    result = build_demucs_separation_command(
                        stem_mode=stem_mode,
                        source_path=source_path,
                        output_directory=output_directory,
                        work_directory=work_directory,
                    )

                    self.assertEqual(result.model_name, model_name)
                    self.assertEqual(
                        result.command,
                        (
                            DEMUCS_PYTHON_EXECUTABLE,
                            DEMUCS_CPU_CLI_SCRIPT,
                            "--device",
                            DEMUCS_DEVICE,
                            "--repo",
                            DEMUCS_MODEL_REPOSITORY,
                            "--name",
                            model_name,
                            "--out",
                            str(output_directory.resolve()),
                            *mode_arguments,
                            str(source_path.resolve()),
                        ),
                    )
                    self.assertEqual(
                        result.expected_output_directory,
                        output_directory.resolve() / model_name / "source",
                    )

    def test_invalid_stem_mode_is_rejected_before_path_inspection(self) -> None:
        """A user/database typo cannot select another model or touch local paths."""

        with self.assertRaises(DemucsCommandContractError):
            build_demucs_separation_command(
                stem_mode="all-stems",
                source_path=Path("/not-inspected"),
                output_directory=Path("/not-inspected"),
                work_directory=Path("/not-inspected"),
            )

    def test_unsafe_source_or_output_paths_never_form_a_command(self) -> None:
        """Host paths, symlinks, names, and old output files are all blocked."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            work_directory = root / "scratch"
            work_directory.mkdir()
            source_path = work_directory / "input" / "source.media"
            source_path.parent.mkdir()
            source_path.write_bytes(b"source")
            output_directory = work_directory / "output"
            output_directory.mkdir()
            outside_source = root / "outside.media"
            outside_source.write_bytes(b"outside")
            symlink_source = work_directory / "linked.media"
            symlink_source.symlink_to(outside_source)
            wrong_name = work_directory / "input" / "browser-filename.wav"
            wrong_name.write_bytes(b"source")

            unsafe_cases = (
                (outside_source, output_directory, "outside-source"),
                (symlink_source, output_directory, "symlink-source"),
                (wrong_name, output_directory, "user-name"),
            )
            for unsafe_source, safe_output, label in unsafe_cases:
                with self.subTest(case=label):
                    with self.assertRaises(DemucsCommandPathError):
                        build_demucs_separation_command(
                            stem_mode="4-stems",
                            source_path=unsafe_source,
                            output_directory=safe_output,
                            work_directory=work_directory,
                        )

            (output_directory / "previous-task.wav").write_bytes(b"artifact")
            with self.assertRaises(DemucsCommandPathError):
                build_demucs_separation_command(
                    stem_mode="4-stems",
                    source_path=source_path,
                    output_directory=output_directory,
                    work_directory=work_directory,
                )


if __name__ == "__main__":
    unittest.main()
