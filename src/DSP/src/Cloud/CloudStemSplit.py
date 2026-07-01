import os
import sys
import argparse
import boto3
import json
from pathlib import Path

# Try importing demucs to ensure the environment is configured correctly
try:
    import demucs.separate
except ImportError:
    print("Error: Demucs is not installed or not found in the container environment.")
    sys.exit(1)

def download_from_s3(s3_client, bucket: str, key: str, local_path: Path):
    print(f"Downloading s3://{bucket}/{key} to {local_path}...")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    s3_client.download_file(bucket, key, str(local_path))
    print("Download complete.")

def upload_directory_to_s3(s3_client, local_dir: Path, bucket: str, prefix: str):
    print(f"Uploading stems from {local_dir} to s3://{bucket}/{prefix} ...")
    uploaded_keys = {}
    for root, dirs, files in os.walk(local_dir):
        for file in files:
            local_file = Path(root) / file
            # Create a relative path for the S3 key
            rel_path = local_file.relative_to(local_dir)
            s3_key = f"{prefix}/{rel_path}"
            print(f"  -> Uploading {file} to {s3_key}")
            s3_client.upload_file(str(local_file), bucket, s3_key)
            
            # Use the filename without extension as the stem identifier (e.g. "vocals", "drums")
            stem_name = local_file.stem
            uploaded_keys[stem_name] = s3_key
            
    print("Upload complete.")
    return uploaded_keys

def split_stems_cloud(input_bucket: str, output_bucket: str, file_key: str, mode: str = "6-stems"):
    s3_client = boto3.client('s3')
    
    # Setup local temporary paths inside the Docker container
    # AWS Batch provides ample space in /tmp, or in a mapped EBS volume.
    base_tmp_dir = Path("/tmp/clouddsp")
    input_filename = Path(file_key).name
    local_input_path = base_tmp_dir / "input" / input_filename
    local_output_dir = base_tmp_dir / "output"
    
    # 1. Fetch S3 Object Metadata
    print(f"Fetching metadata for s3://{input_bucket}/{file_key}...")
    head_response = s3_client.head_object(Bucket=input_bucket, Key=file_key)
    metadata = head_response.get('Metadata', {})
    
    connection_id = metadata.get('connection-id', 'unknown')
    s3_mode = metadata.get('stem-mode')
    
    if s3_mode:
        print(f"Detected stem-mode in S3 metadata. Overriding default mode: {s3_mode}")
        mode = s3_mode
        
    print(f"WebSocket Connection ID associated with this file: {connection_id}")

    # 2. Download the audio file from S3
    download_from_s3(s3_client, input_bucket, file_key, local_input_path)
    
    # 2. Configure Demucs
    print(f"Starting Demucs separation in mode: {mode}")
    args = []
    model_name = ""
    
    if mode == "6-stems":
        model_name = "htdemucs_6s"
        args = ["-n", model_name, "-o", str(local_output_dir), str(local_input_path)]
    elif mode == "4-stems":
        model_name = "htdemucs"
        args = ["-n", model_name, "-o", str(local_output_dir), str(local_input_path)]
    elif mode == "2-stems":
        model_name = "htdemucs"
        args = ["-n", model_name, "--two-stems", "vocals", "-o", str(local_output_dir), str(local_input_path)]
    else:
        print(f"Error: Invalid mode '{mode}'. Choose 2-stems, 4-stems, or 6-stems.")
        sys.exit(1)
        
    # 3. Run Demucs
    try:
        demucs.separate.main(args)
        print("\nSeparation complete locally!")
    except Exception as e:
        print(f"\nAn error occurred during separation: {e}")
        sys.exit(1)
        
    # Demucs outputs to: output_dir / model_name / input_filename_without_extension
    demucs_output_path = local_output_dir / model_name / local_input_path.stem
    
    if not demucs_output_path.exists():
        print(f"Error: Could not find output directory {demucs_output_path}")
        sys.exit(1)
        
    # 4. Upload the resulting stems back to S3
    # We will store them under 'stems/<original_filename_without_extension>/'
    s3_output_prefix = f"stems/{local_input_path.stem}"
    uploaded_keys = upload_directory_to_s3(s3_client, demucs_output_path, output_bucket, s3_output_prefix)
    
    # 5. Generate Presigned GET URLs for the uploaded stems
    print("\nGenerating pre-signed download URLs...")
    download_urls = {}
    for stem_name, key in uploaded_keys.items():
        url = s3_client.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': output_bucket,
                'Key': key
            },
            ExpiresIn=3600 # 1 hour
        )
        download_urls[stem_name] = url
        
    # 6. Notify the Frontend via WebSocket API
    ws_url = os.environ.get('WEBSOCKET_API_URL')
    if ws_url and connection_id and connection_id != 'unknown':
        print(f"Sending completion event to WebSocket ID: {connection_id}")
        apigw_client = boto3.client('apigatewaymanagementapi', endpoint_url=ws_url)
        
        payload = json.dumps({
            "type": "processing_complete",
            "stems": download_urls
        })
        
        try:
            apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=payload
            )
            print("Successfully notified frontend!")
        except Exception as e:
            print(f"Warning: Failed to send WebSocket message: {e}")
    else:
        print("Skipping WebSocket notification (Missing URL or Connection ID).")
    
    print("\nCloud stem splitting job completed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cloud-native Stem Splitter using Demucs and S3.")
    
    # AWS Batch allows overriding commands or passing environment variables.
    # We support both CLI arguments and Environment Variables as fallbacks.
    parser.add_argument("--input-bucket", type=str, default=os.environ.get("INPUT_BUCKET"),
                        help="S3 bucket containing the input audio")
    parser.add_argument("--output-bucket", type=str, default=os.environ.get("OUTPUT_BUCKET"),
                        help="S3 bucket to upload the separated stems")
    parser.add_argument("--file-key", type=str, default=os.environ.get("FILE_KEY"),
                        help="The S3 key of the audio file to process")
    parser.add_argument("-m", "--mode", type=str, default=os.environ.get("STEM_MODE", "6-stems"),
                        choices=["2-stems", "4-stems", "6-stems"],
                        help="Stem splitting mode")
    
    args = parser.parse_args()
    
    if not args.input_bucket or not args.output_bucket or not args.file_key:
        print("Error: Missing required S3 parameters.")
        print("You must provide --input-bucket, --output-bucket, and --file-key (or set their ENVs).")
        parser.print_help()
        sys.exit(1)
        
    split_stems_cloud(args.input_bucket, args.output_bucket, args.file_key, args.mode)
