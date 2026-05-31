import os
import numpy as np
from pedalboard.io import AudioFile

def apply_ring_mod(input_file, output_file, frequency_hz=600.0, mix=1.0):
    print(f"Applying Ring Modulator (Frequency: {frequency_hz}Hz, Mix: {mix}) to {input_file}")
    
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found.")
        return False
        
    with AudioFile(input_file) as f:
        audio = f.read(f.frames)
        samplerate = f.samplerate
        
    num_samples = audio.shape[1]
    t = np.arange(num_samples) / samplerate
    carrier = np.sin(2 * np.pi * frequency_hz * t)
    
    wet_audio = audio * carrier
    effected_audio = ((1.0 - mix) * audio) + (mix * wet_audio)
    
    with AudioFile(output_file, 'w', samplerate, effected_audio.shape[0]) as f:
        f.write(effected_audio.astype(np.float32))
        
    print(f"Successfully saved processed audio to {output_file}")
    return True

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    print("\n[TEST] Running standalone ring modulator test...")
    apply_ring_mod("PYT-sample.wav", "test_out_ring_mod.wav")