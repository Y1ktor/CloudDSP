import os
import json
import argparse
from pathlib import Path

# Try importing librosa to use its built-in frequency-to-note converter
try:
    import librosa
except ImportError:
    print("Error: Librosa is required for accurate Hz to Musical Pitch conversion.")
    import sys
    sys.exit(1)

def extract_notes_from_mir(input_file: str, output_file: str):
    """
    Parses Librosa MIR output JSON, merging onsets and pitch contours to create a 
    discrete list of musical notes (timestamp and pitch name).
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        return

    print(f"Processing MIR data from {input_path.name}...")
    
    with open(input_path, 'r') as f:
        data = json.load(f)

    onsets = data.get("onsets", [])
    pitch_contour = data.get("pitch_contour", [])

    if not onsets or not pitch_contour:
        print("Error: JSON must contain both 'onsets' and 'pitch_contour' arrays.")
        return

    notes_data = {
        "filename": data.get("filename", "unknown"),
        "stem_type": data.get("stem_type", "unknown"),
        "notes": []
    }

    print(f"Found {len(onsets)} potential note onsets.")

    # Convert pitch contour to a dictionary for incredibly fast O(1) time lookups.
    # Since floating point times might not match exactly, we'll implement a closest-match search.
    
    for onset_time in onsets:
        # Wait 50ms after the onset to sample the pitch contour.
        # This gives the new note time to bloom and overshadow any decaying resonance 
        # from the previous note, while remaining responsive to fast playing.
        sample_time = onset_time + 0.05
        closest_frame = None
        
        for frame in pitch_contour:
            if frame["time"] >= sample_time:
                closest_frame = frame
                break # We found the frame just after the blooming period
                
        # Fallback: If no later frame exists (e.g. onset is at the very end of the file),
        # use the last known frame in the pitch contour.
        if not closest_frame:
            closest_frame = pitch_contour[-1]
            
        if "notes" in closest_frame:
            # Polyphonic Chromagram case: we already have exact pitch classes
            notes_data["notes"].append({
                "time": onset_time,
                "pitch": closest_frame["notes"]
            })
        else:
            hz_data = closest_frame["hz"]
            
            if isinstance(hz_data, list):
                # Legacy polyphonic case (raw Hz)
                note_names = []
                for h in hz_data:
                    raw_name = librosa.hz_to_note(h)
                    note_names.append(raw_name.replace('♯', '#').replace('♭', 'b'))
                
                if note_names:
                    notes_data["notes"].append({
                        "time": onset_time,
                        "pitch": note_names
                    })
            else:
                # Monophonic case: hz_data is a single float
                raw_note_name = librosa.hz_to_note(hz_data)
                note_name = raw_note_name.replace('♯', '#').replace('♭', 'b')
                
                notes_data["notes"].append({
                    "time": onset_time,
                    "pitch": note_name
                })

    # Save the processed discrete notes to JSON
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, 'w') as f:
        json.dump(notes_data, f, indent=2)
        
    print(f"Successfully extracted {len(notes_data['notes'])} discrete notes.")
    print(f"Saved note sequence to {out_path}")


if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    
    # Default paths
    default_input = project_root / "output" / "analysis" / "guitar_data.json"
    default_output = project_root / "output" / "analysis" / "guitar_notes.json"
    
    parser = argparse.ArgumentParser(description="Convert raw MIR data into discrete musical notes.")
    parser.add_argument("-i", "--input", type=str, default=str(default_input),
                        help="Path to raw MIR JSON file")
    parser.add_argument("-o", "--output", type=str, default=str(default_output),
                        help="Path to save processed Notes JSON")
    
    args = parser.parse_args()
    
    extract_notes_from_mir(args.input, args.output)