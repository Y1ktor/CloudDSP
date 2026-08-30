import os
import argparse
import sys
import json
from pathlib import Path

# Try importing librosa, numpy and scipy
try:
    import librosa
    import numpy as np
    import scipy.signal
except ImportError:
    print("Error: Librosa, NumPy or SciPy is not installed or not found in the current environment.")
    print("Please run: pip install librosa numpy scipy")
    sys.exit(1)

def extract_music_info(input_file: str, output_file: str, stem_type: str = "auto"):
    """
    Extracts music information from an audio stem using Librosa.
    
    Args:
        input_file: Path to the input audio stem (.wav).
        output_file: Path to save the extracted JSON data.
        stem_type: Hint for processing (e.g., 'drums', 'bass', 'vocals'). 
                   If 'drums', focuses on onset/beat detection. 
                   If 'bass' or 'vocals', focuses on pitch tracking.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        sys.exit(1)
        
    print(f"Loading {input_path.name} into Librosa...")
    # Load audio. sr=None preserves original sample rate.
    # We mix down to mono (mono=True) as pitch/onset tracking usually expects a 1D array.
    y, sr = librosa.load(input_path, sr=None, mono=True)
    
    results = {
        "filename": input_path.name,
        "sample_rate": sr,
        "duration_sec": librosa.get_duration(y=y, sr=sr),
        "stem_type": stem_type
    }

    print("Extracting features...")

    # 1. Onset Detection
    # This finds the exact timestamps where a new note/beat/chord begins.
    print(" -> Detecting onsets...")
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    results["onsets"] = [round(float(t), 3) for t in onset_times]

    # 2. Pitch Tracking
    if stem_type in ["piano", "guitar", "other"]:
        print(" -> Tracking polyphonic pitch (Chromagram)...")
        results["is_polyphonic"] = True
        
        if stem_type == 'piano':
            fmin = librosa.note_to_hz('A0')
            n_octaves = 7
        elif stem_type == 'guitar':
            fmin = librosa.note_to_hz('E2')
            n_octaves = 6
        else:
            fmin = librosa.note_to_hz('C2')
            n_octaves = 6
            
        # Compute Chromagram (Instantaneous, no time-smearing)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, fmin=fmin, n_octaves=n_octaves)
        times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr)
        
        pitch_classes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        
        f0_contour = []
        for i in range(chroma.shape[1]):
            frame_chroma = chroma[:, i]
            
            # Combine Fix 1 & 2: Dynamic Amplitude Thresholding
            max_energy = np.max(frame_chroma)
            
            # Only process if there's actually a significant sound
            if max_energy > 0.15:
                # Keep notes that have at least 60% of the energy of the loudest note,
                # with a hard minimum floor of 0.25
                dynamic_threshold = max(0.25, max_energy * 0.6)
                active_bins = np.where(frame_chroma >= dynamic_threshold)[0]
            else:
                active_bins = []
            
            if len(active_bins) > 0:
                notes = [pitch_classes[b] for b in active_bins]
                f0_contour.append({
                    "time": round(float(times[i]), 3),
                    "notes": notes
                })
            else:
                f0_contour.append({
                    "time": round(float(times[i]), 3),
                    "notes": []
                })
        results["pitch_contour"] = f0_contour

    elif stem_type in ["vocals", "bass", "auto"]:
        print(" -> Tracking monophonic pitch (f0)...")
        results["is_polyphonic"] = False
        
        if stem_type == 'bass':
            fmin = librosa.note_to_hz('B0')
            fmax = librosa.note_to_hz('C5')
        elif stem_type == 'vocals':
            fmin = librosa.note_to_hz('C2')
            fmax = librosa.note_to_hz('C6')
        else:
            fmin = librosa.note_to_hz('C2')
            fmax = librosa.note_to_hz('C7')
            
        # pyin returns fundamental frequency (f0), voiced flag, and voiced probabilities
        f0, voiced_flag, voiced_probs = librosa.pyin(y, sr=sr, fmin=fmin, fmax=fmax)
        
        times = librosa.times_like(f0, sr=sr)
        f0_contour = []
        
        for i in range(len(f0)):
            # Only record pitch if it is considered voiced and not NaN
            if voiced_flag[i] and not np.isnan(f0[i]):
                f0_contour.append({
                    "time": round(float(times[i]), 3),
                    "hz": round(float(f0[i]), 2)
                })
                
        results["pitch_contour"] = f0_contour

    # Ensure output directory exists
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save to JSON
    print(f"Saving data to {out_path}...")
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
        
    print("Done!")

if __name__ == "__main__":
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    
    # Example default paths
    default_input = project_root / "output" / "stems" / "htdemucs_6s" / "Yosemite" / "guitar.wav"
    default_output = project_root / "output" / "analysis" / "guitar_data.json"
    
    parser = argparse.ArgumentParser(description="Extract Music Information (Pitch, Onsets) using Librosa.")
    parser.add_argument("-i", "--input", type=str, default=str(default_input),
                        help="Path to input audio stem (.wav)")
    parser.add_argument("-o", "--output", type=str, default=str(default_output),
                        help="Path to save output JSON data")
    parser.add_argument("-t", "--type", type=str, default="auto", choices=["drums", "vocals", "bass", "piano", "guitar", "other", "auto"],
                        help="Type of stem to optimize processing (drums = onsets, vocals/bass = pitch)")
    
    args = parser.parse_args()
    
    extract_music_info(args.input, args.output, args.type)
