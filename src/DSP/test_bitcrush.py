import os
import numpy as np
from pedalboard import Pedalboard, Bitcrush
from pedalboard.io import AudioFile

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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    print("\n[TEST] Running standalone bitcrush test...")
    apply_bitcrush("PYT-sample.wav", "test_out_bitcrush.wav")