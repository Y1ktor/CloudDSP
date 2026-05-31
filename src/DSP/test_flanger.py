import os
from pedalboard import Pedalboard, Chorus
from pedalboard.io import AudioFile

def apply_flanger(input_file, output_file, rate_hz=0.5, depth=0.8, centre_delay_ms=2.0, feedback=0.7, mix=0.5):
    print(f"Applying Flanger (Rate: {rate_hz}Hz, Depth: {depth}, Delay: {centre_delay_ms}ms, Feedback: {feedback}, Mix: {mix}) to {input_file}")
    
    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found.")
        return False
        
    with AudioFile(input_file) as f:
        audio = f.read(f.frames)
        samplerate = f.samplerate
    
    board = Pedalboard([
        Chorus(
            rate_hz=rate_hz,
            depth=depth,
            centre_delay_ms=centre_delay_ms,
            feedback=feedback,
            mix=mix
        )
    ])
    
    effected_audio = board(audio, samplerate)
    
    with AudioFile(output_file, 'w', samplerate, effected_audio.shape[0]) as f:
        f.write(effected_audio)
        
    print(f"Successfully saved processed audio to {output_file}")
    return True

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    print("\n[TEST] Running standalone flanger test...")
    apply_flanger("PYT-sample.wav", "test_out_flanger.wav")