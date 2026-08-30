import os
import sys
import numpy as np
from pedalboard import Pedalboard, Bitcrush
from pedalboard.io import AudioFile

# Add the src directory to the path so we can test the actual logic if needed later
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

def apply_bitcrush(input_file, output_file, bit_depth=4, decimation_factor=4):
    print(f"Applying Bitcrush (bit depth: {bit_depth}, decimation factor: {decimation_factor}x) to {input_file}")
    
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found.")
        return False
        
    with AudioFile(input_file) as f:
        audio = f.read(f.frames)
        samplerate = f.samplerate
    
    if decimation_factor > 1:
        decimated_audio = np.repeat(audio[:, ::decimation_factor], decimation_factor, axis=1)
        audio = decimated_audio[:, :audio.shape[1]]
    
    board = Pedalboard([Bitcrush(bit_depth=bit_depth)])
    effected_audio = board(audio, samplerate)
    
    with AudioFile(output_file, 'w', samplerate, effected_audio.shape[0]) as f:
        f.write(effected_audio)
        
    print(f"Successfully saved processed audio to {output_file}")
    return True

if __name__ == "__main__":
    # Ensure the script runs relative to the tests directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    # Point to the sample file in the assets directory
    input_wav = "../assets/PYT-sample.wav"
    output_wav = "test_out_bitcrush.wav"
    
    print("\n[TEST] Running standalone bitcrush test...")
    apply_bitcrush(input_wav, output_wav)