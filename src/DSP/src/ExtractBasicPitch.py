import os
import argparse
from pathlib import Path
import sys

try:
    from basic_pitch.inference import predict_and_save
except ImportError:
    print("Error: basic-pitch is not installed.")
    print("Please run: pip install 'basic-pitch[core]'")
    sys.exit(1)

def extract_midi_basic_pitch(input_file: str, output_dir: str):
    """
    Extracts polyphonic notes from an audio file using Spotify's Basic Pitch neural network
    and saves the output as a MIDI file.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        sys.exit(1)
        
    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Running Basic Pitch on {input_path.name}...")
    
    try:
        # Run the Basic Pitch prediction
        # predict_and_save automatically names the output file based on the input name
        predict_and_save(
            audio_path_list=[str(input_path)],
            output_directory=str(out_dir_path),
            save_midi=True,
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False
        )
        print(f"Done! MIDI file saved to {out_dir_path}")
    except Exception as e:
        print(f"An error occurred during Basic Pitch processing: {e}")

if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    
    # Default paths
    default_input = project_root / "output" / "stems" / "htdemucs_6s" / "Yosemite" / "piano.wav"
    default_output_dir = project_root / "output" / "analysis"
    
    parser = argparse.ArgumentParser(description="Extract MIDI using Spotify Basic Pitch.")
    parser.add_argument("-i", "--input", type=str, default=str(default_input),
                        help="Path to input audio stem (.wav)")
    parser.add_argument("-o", "--output-dir", type=str, default=str(default_output_dir),
                        help="Path to output directory where the .mid file will be saved")
    
    args = parser.parse_args()
    
    extract_midi_basic_pitch(args.input, args.output_dir)
