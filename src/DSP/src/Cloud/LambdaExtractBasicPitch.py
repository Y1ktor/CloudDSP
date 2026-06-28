import os
import sys
import boto3
import json
import urllib.parse
from pathlib import Path

# ==========================================
# TFLITE MOCK TENSORFLOW MODULE HACK
# ==========================================
# Since we installed basic_pitch without tensorflow (to save 1.5GB) and instead use tflite-runtime,
# we need to mock the `tensorflow` module so that `basic_pitch` doesn't crash on import.
import types
if 'tensorflow' not in sys.modules:
    dummy_tf = types.ModuleType('tensorflow')
    sys.modules['tensorflow'] = dummy_tf

# Now we can safely import basic_pitch and librosa
try:
    from basic_pitch.inference import predict_and_save
    from basic_pitch import ICASSP_2022_MODEL_PATH
    import librosa
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)

def download_from_s3(s3_client, bucket: str, key: str, local_path: Path):
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))
    print("Download complete.")

def upload_file_to_s3(s3_client, local_file: Path, bucket: str, s3_key: str):
    print(f"Uploading {local_file.name} to s3://{bucket}/{s3_key} ...")
    s3_client.upload_file(str(local_file), bucket, s3_key)
    print("Upload complete.")

def extract_midi_cloud(input_bucket: str, output_bucket: str, file_key: str):
    s3_client = boto3.client('s3')
    
    # Lambda provides 512MB to 10GB of temporary storage in /tmp
    base_tmp_dir = Path("/tmp/clouddsp")
    input_filename = Path(file_key).name
    local_input_path = base_tmp_dir / "input" / input_filename
    local_output_dir = base_tmp_dir / "output"
    local_output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Download the audio stem from S3
    download_from_s3(s3_client, input_bucket, file_key, local_input_path)
    
    # 2. Run Basic Pitch
    print(f"Running Basic Pitch on {input_filename}...")
    try:
        predict_and_save(
            audio_path_list=[str(local_input_path)],
            output_directory=str(local_output_dir),
            save_midi=True,
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
            model_or_model_path=ICASSP_2022_MODEL_PATH
        )
        print("Basic Pitch inference complete!")
    except Exception as e:
        print(f"An error occurred during Basic Pitch processing: {e}")
        raise e
        
    # 3. Find the generated MIDI file
    expected_midi_name = f"{local_input_path.stem}_basic_pitch.mid"
    midi_file_path = local_output_dir / expected_midi_name
    
    if not midi_file_path.exists():
        midi_files = list(local_output_dir.glob("*.mid"))
        if not midi_files:
            raise FileNotFoundError("No MIDI file was generated.")
        midi_file_path = midi_files[0]
        
    # 4. Extract BPM using Librosa
    print("Extracting BPM using librosa...")
    bpm_file_path = None
    try:
        y, sr = librosa.load(str(local_input_path))
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if hasattr(tempo, "__iter__") else float(tempo)
        
        bpm_data = {"bpm": bpm}
        bpm_file_path = local_output_dir / f"{local_input_path.stem}_bpm.json"
        with open(bpm_file_path, "w") as f:
            json.dump(bpm_data, f)
    except Exception as e:
        print(f"Warning: Failed to extract BPM: {e}")
        
    # 5. Upload the resulting MIDI and BPM JSON files back to S3
    parts = Path(file_key).parts
    if len(parts) >= 3 and parts[0] == "stems":
        uuid_folder = parts[1]
        s3_output_key = f"midi/{uuid_folder}/{local_input_path.stem}.mid"
        s3_bpm_key = f"midi/{uuid_folder}/{local_input_path.stem}_bpm.json"
    else:
        s3_output_key = f"midi/{local_input_path.stem}.mid"
        s3_bpm_key = f"midi/{local_input_path.stem}_bpm.json"
        
    upload_file_to_s3(s3_client, midi_file_path, output_bucket, s3_output_key)
    
    if bpm_file_path and bpm_file_path.exists():
        upload_file_to_s3(s3_client, bpm_file_path, output_bucket, s3_bpm_key)
    
    print("\nLambda Basic Pitch job completed successfully.")

def lambda_handler(event, context):
    """
    AWS Lambda Entry Point.
    This function is triggered by S3 events (e.g., ObjectCreated) or SQS queues.
    """
    print("Received event:", json.dumps(event))
    
    # OUTPUT_BUCKET can be set in Lambda Environment Variables
    output_bucket = os.environ.get("OUTPUT_BUCKET")
    
    try:
        # Loop through all records in the event
        for record in event.get('Records', []):
            
            # Scenario A: Triggered via an SQS Queue wrapped around an S3 Event
            if 'body' in record:
                body = json.loads(record['body'])
                if 'Records' in body:
                    s3_event = body['Records'][0]
                    bucket = s3_event['s3']['bucket']['name']
                    # Unquote handles URL encoded characters like spaces (+) in the file name
                    key = urllib.parse.unquote_plus(s3_event['s3']['object']['key'])
                    
                    if not output_bucket:
                        output_bucket = bucket
                    
                    extract_midi_cloud(bucket, output_bucket, key)
                    continue

            # Scenario B: Triggered directly from an S3 Event Notification
            if 's3' in record:
                bucket = record['s3']['bucket']['name']
                key = urllib.parse.unquote_plus(record['s3']['object']['key'])
                
                if not output_bucket:
                    output_bucket = bucket
                
                extract_midi_cloud(bucket, output_bucket, key)
                
        return {
            'statusCode': 200,
            'body': json.dumps('Successfully processed audio.')
        }
        
    except Exception as e:
        print(f"Error processing event: {e}")
        # Return a 500 error so AWS Lambda/SQS knows the job failed and can automatically retry it
        return {
            'statusCode': 500,
            'body': json.dumps(f"Error: {str(e)}")
        }
