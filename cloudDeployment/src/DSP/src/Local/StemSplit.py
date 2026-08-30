import os
import argparse
import sys
from pathlib import Path

# Try importing demucs to ensure the environment is configured correctly
try:
    import demucs.separate
except ImportError:
    print("Error: Demucs is not installed or not found in the current environment.")
    print("Please ensure you run this script within the activated virtual environment.")
    sys.exit(1)

def split_stems(input_file: str, output_dir: str, mode: str = "6-stems"):
    """
    Splits an audio file into stems using Demucs.
    
    Args:
        input_file: Path to the input audio file.
        output_dir: Path to save the separated stems.
        mode: The stem splitting mode ('2-stems', '4-stems', or '6-stems').
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        sys.exit(1)
        
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Starting Demucs separation...")
    print(f"Input: {input_path}")
    print(f"Output Directory: {out_path}")
    print(f"Mode: {mode}")
    
    args = []
    model_name = ""
    
    # Configure Demucs arguments based on the selected mode
    if mode == "6-stems":
        model_name = "htdemucs_6s"
        args = ["-n", model_name, "-o", str(out_path), str(input_path)]
    elif mode == "4-stems":
        model_name = "htdemucs"
        args = ["-n", model_name, "-o", str(out_path), str(input_path)]
    elif mode == "2-stems":
        model_name = "htdemucs"
        args = ["-n", model_name, "--two-stems", "vocals", "-o", str(out_path), str(input_path)]
    else:
        print(f"Error: Invalid mode '{mode}'. Choose 2-stems, 4-stems, or 6-stems.")
        sys.exit(1)
    
    try:
        # Run the separation process
        demucs.separate.main(args)
        print("\nSeparation complete!")
        print(f"Stems are saved in: {out_path / model_name / input_path.stem}")
    except Exception as e:
        print(f"\nAn error occurred during separation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Setup paths relative to this script
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    
    # Default paths based on your architecture
    default_input = project_root / "assets" / "PYT-sample.wav"
    default_output = project_root / "output" / "stems"
    
    parser = argparse.ArgumentParser(description="Split audio into stems using Demucs.")
    parser.add_argument("-i", "--input", type=str, default=str(default_input),
                        help="Path to input audio file")
    parser.add_argument("-o", "--output", type=str, default=str(default_output),
                        help="Path to output directory")
    parser.add_argument("-m", "--mode", type=str, default="6-stems",
                        choices=["2-stems", "4-stems", "6-stems"],
                        help="Stem splitting mode: 2-stems, 4-stems, or 6-stems")
    
    args = parser.parse_args()
    
    split_stems(args.input, args.output, args.mode)
