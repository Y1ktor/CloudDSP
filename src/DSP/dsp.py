import json
import os
import boto3
import numpy as np
from pedalboard import Pedalboard, Bitcrush, Chorus
from pedalboard.io import AudioFile

# Initialize AWS clients outside the handler for connection reuse
s3_client = boto3.client('s3')

# Environment variables for bucket names (using placeholders as fallbacks)
INCOMING_BUCKET = os.environ.get('INCOMING_BUCKET_NAME', 'placeholder-incoming-bucket')
PROCESSED_BUCKET = os.environ.get('PROCESSED_BUCKET_NAME', 'placeholder-processed-bucket')

# ==========================================
# DSP EFFECT FUNCTIONS
# ==========================================

def apply_bitcrush(input_file, output_file, bit_depth=4, decimation_factor=4):
    print(f"Applying Bitcrush (bit depth: {bit_depth}, decimation factor: {decimation_factor}x)")
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

def apply_flanger(input_file, output_file, rate_hz=0.5, depth=0.8, centre_delay_ms=2.0, feedback=0.7, mix=0.5):
    print(f"Applying Flanger (Rate: {rate_hz}Hz, Depth: {depth}, Delay: {centre_delay_ms}ms)")
    with AudioFile(input_file) as f:
        audio = f.read(f.frames)
        samplerate = f.samplerate
        
    board = Pedalboard([
        Chorus(rate_hz=rate_hz, depth=depth, centre_delay_ms=centre_delay_ms, feedback=feedback, mix=mix)
    ])
    effected_audio = board(audio, samplerate)
    
    with AudioFile(output_file, 'w', samplerate, effected_audio.shape[0]) as f:
        f.write(effected_audio)

def apply_ring_mod(input_file, output_file, frequency_hz=600.0, mix=1.0):
    print(f"Applying Ring Modulator (Frequency: {frequency_hz}Hz)")
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


# ==========================================
# LAMBDA HANDLER
# ==========================================

def lambda_handler(event, context):
    """
    AWS Lambda entry point. Triggered by SQS messages.
    """
    print(f"Received SQS event with {len(event.get('Records', []))} records.")
    
    for record in event.get('Records', []):
        try:
            # 1. Parse the SQS Message Body
            payload = json.loads(record['body'])
            job_id = payload.get('job_id', 'unknown_job')
            file_key = payload.get('file_key')
            effect_type = payload.get('effect')
            parameters = payload.get('parameters', {})
            
            if not file_key or not effect_type:
                print(f"Skipping record due to missing file_key or effect: {payload}")
                continue
                
            print(f"--- Processing Job ID: {job_id} | File: {file_key} | Effect: {effect_type} ---")
            
            # Lambda requires using /tmp/ for ephemeral storage
            # Ensure unique filenames in case of concurrent execution environments
            local_input_path = f"/tmp/input_{job_id}.wav"
            local_output_path = f"/tmp/output_{job_id}.wav"
            processed_s3_key = f"processed/{job_id}_{effect_type}.wav"
            
            # 2. Download the source file from S3
            print(f"Downloading s3://{INCOMING_BUCKET}/{file_key} to {local_input_path}")
            s3_client.download_file(INCOMING_BUCKET, file_key, local_input_path)
            
            # 3. Route to the correct DSP function
            if effect_type == 'bitcrush':
                bit_depth = parameters.get('bitrate', 8)
                decimation_factor = parameters.get('decimation', 4)
                apply_bitcrush(local_input_path, local_output_path, bit_depth, decimation_factor)
                
            elif effect_type == 'flanger':
                rate_hz = parameters.get('rate_hz', 0.5)
                depth = parameters.get('depth', 0.5)
                centre_delay_ms = parameters.get('delay_ms', 2.0)
                feedback = parameters.get('feedback', 0.7)
                mix = parameters.get('mix', 0.5)
                apply_flanger(local_input_path, local_output_path, rate_hz, depth, centre_delay_ms, feedback, mix)
                
            elif effect_type == 'ring_mod':
                frequency_hz = parameters.get('frequency_hz', 500.0)
                mix = parameters.get('mix', 1.0)
                apply_ring_mod(local_input_path, local_output_path, frequency_hz, mix)
                
            else:
                print(f"Unknown effect '{effect_type}'. Skipping processing.")
                continue
                
            # 4. Upload the processed file back to S3
            print(f"Uploading {local_output_path} to s3://{PROCESSED_BUCKET}/{processed_s3_key}")
            s3_client.upload_file(local_output_path, PROCESSED_BUCKET, processed_s3_key)
            print(f"Successfully processed Job ID: {job_id}")

        except Exception as e:
            print(f"Error processing record: {e}")
            # If you want SQS to retry this message, you should raise the exception here.
            # raise e
            
        finally:
            # 5. Clean up /tmp/ storage to prevent out-of-disk-space errors on warm starts
            if 'local_input_path' in locals() and os.path.exists(local_input_path):
                os.remove(local_input_path)
            if 'local_output_path' in locals() and os.path.exists(local_output_path):
                os.remove(local_output_path)

    return {
        'statusCode': 200,
        'body': json.dumps('SQS Batch processing complete')
    }
