import os
import sys
import argparse
import boto3
from pathlib import Path

# Try importing basic_pitch
try:
    from basic_pitch.inference import predict_and_save
    from basic_pitch import ICASSP_2022_MODEL_PATH
except ImportError:
    print("Error: basic_pitch is not installed in the container environment.")
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
    
    # Setup local temporary paths inside the Docker container
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
        sys.exit(1)
        
    # 3. Find the generated MIDI file
    # Basic Pitch usually appends '_basic_pitch.mid' to the original filename
    expected_midi_name = f"{local_input_path.stem}_basic_pitch.mid"
    midi_file_path = local_output_dir / expected_midi_name
    
    # Fallback to just grabbing the first .mid file if the naming convention changes
    if not midi_file_path.exists():
        midi_files = list(local_output_dir.glob("*.mid"))
        if not midi_files:
            print("Error: No MIDI file was generated.")
            sys.exit(1)
        midi_file_path = midi_files[0]
        
    # 4. Upload the resulting MIDI file back to S3
    # If the input was `stems/1234-uuid-mysong/piano.wav`, we save it to `midi/1234-uuid-mysong/piano.mid`
    parts = Path(file_key).parts
    if len(parts) >= 3 and parts[0] == "stems":
        # parts = ('stems', '1234-uuid-mysong', 'piano.wav')
        uuid_folder = parts[1]
        s3_output_key = f"midi/{uuid_folder}/{local_input_path.stem}.mid"
    else:
        # Fallback if structure is different
        s3_output_key = f"midi/{local_input_path.stem}.mid"
        
    upload_file_to_s3(s3_client, midi_file_path, output_bucket, s3_output_key)
    
    print("\nCloud Basic Pitch job completed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cloud-native MIDI Extraction using Basic Pitch and S3.")
    
    # Support both CLI arguments and AWS Batch Environment Variables
    parser.add_argument("--input-bucket", type=str, default=os.environ.get("INPUT_BUCKET"),
                        help="S3 bucket containing the input audio stem")
    parser.add_argument("--output-bucket", type=str, default=os.environ.get("OUTPUT_BUCKET"),
                        help="S3 bucket to upload the generated MIDI file")
    parser.add_argument("--file-key", type=str, default=os.environ.get("FILE_KEY"),
                        help="The S3 key of the audio stem to process")
    
    args = parser.parse_args()
    
    if not args.input_bucket or not args.output_bucket or not args.file_key:
        print("Error: Missing required S3 parameters.")
        print("You must provide --input-bucket, --output-bucket, and --file-key (or set their ENVs).")
        parser.print_help()
        sys.exit(1)
        
    extract_midi_cloud(args.input_bucket, args.output_bucket, args.file_key)
