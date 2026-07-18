"""
AWS Lambda: Audio Extraction via yt-dlp (WebSocket Route)

This Lambda function acts as a WebSocket backend for CloudDSP. When a user provides a media link 
(e.g., YouTube, SoundCloud) through the React frontend, this function handles the entire extraction 
and storage lifecycle.

Workflow:
1. Triggered via API Gateway WebSocket route with a payload containing a 'url'.
2. Validates the URL and enforces an 8-minute maximum duration limit.
3. Uses yt-dlp to fetch the highest quality audio stream (ignoring playlists).
4. Uses ffmpeg (via postprocessor) to convert the downloaded stream into a clean MP3 file.
5. Uploads the MP3 to an S3 bucket, injecting the WebSocket 'connection-id' into the S3 object's metadata. 
   (This allows downstream triggers, like AWS Batch, to know who to notify when stem splitting completes).
6. Generates a short-lived, pre-signed S3 download URL for the uploaded MP3.
7. Pushes the pre-signed URL back to the React client in real-time over the active WebSocket connection, 
   allowing the frontend to play the original track locally.
"""
import os
import json
import boto3
import uuid
import yt_dlp
from botocore.exceptions import ClientError

s3_client = boto3.client('s3')

# We can retrieve the target bucket from an environment variable
BUCKET_NAME = os.environ.get('UPLOAD_BUCKET', 'clouddsp-uploads-bucket')

def send_ws_message(apigw_client, connection_id, payload):
    """Helper to send WebSocket messages."""
    if apigw_client and connection_id:
        try:
            apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(payload)
            )
        except Exception as e:
            print(f"Failed to send WS message to {connection_id}: {str(e)}")

def lambda_handler(event, context):
    """
    AWS Lambda function to extract audio from a given URL using yt-dlp and ffmpeg,
    and save the output MP3 file to S3. Operates via WebSocket.
    """
    
    # 1. Parse Connection ID and WebSocker URL
    request_context = event.get('requestContext', {})
    
    # Connection ID might be injected by API Gateway or explicitly in the body
    connection_id = request_context.get('connectionId')
    
    # Try to construct the WebSocket URL dynamically, or fallback to Env Var
    domain_name = request_context.get('domainName')
    stage = request_context.get('stage')
    
    if domain_name and stage:
        ws_url = f"https://{domain_name}/{stage}"
    else:
        ws_url = os.environ.get('WEBSOCKET_API_URL')
        
    apigw_client = None
    if ws_url:
        apigw_client = boto3.client('apigatewaymanagementapi', endpoint_url=ws_url)

    try:
        # Parse the input event
        body = event.get('body', '{}')
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = event
        else:
            body = event
            
        # Fallback if connectionId was explicitly sent in the body payload
        if not connection_id:
            connection_id = body.get('connectionId')
            
        url = body.get('url')
        
        if not url:
            error_payload = {'type': 'error', 'message': 'Missing required parameter: url'}
            send_ws_message(apigw_client, connection_id, error_payload)
            return {'statusCode': 400, 'body': json.dumps(error_payload)}
            
        print(f"Processing URL: {url} for Connection ID: {connection_id}")
        
        # Send an acknowledgement that we started downloading
        send_ws_message(apigw_client, connection_id, {
            'type': 'status', 
            'message': 'Validating URL and starting extraction...'
        })
        
        # Define temporary paths
        temp_id = str(uuid.uuid4())
        output_template = f"/tmp/{temp_id}.%(ext)s"
        
        # Duration filter function (8 minutes = 480 seconds)
        def check_duration(info, *, incomplete):
            duration = info.get('duration')
            if duration and duration > 480:
                raise ValueError('Audio exceeds the maximum allowed duration of 8 minutes.')
            return None
        
        # Configure yt-dlp options
        # We specify bestaudio and use the postprocessor to convert to mp3 via ffmpeg
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'match_filter': check_duration,
            'noplaylist': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
        }
        
        # Download and extract info
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # extract_info validates the URL and downloads the file
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Extracted_Audio').replace('/', '_').replace('\\', '_')
            
        # The postprocessor creates an .mp3 file
        final_file = f"/tmp/{temp_id}.mp3"
        
        if not os.path.exists(final_file):
            error_payload = {'type': 'error', 'message': 'Failed to extract and convert audio to MP3'}
            send_ws_message(apigw_client, connection_id, error_payload)
            return {'statusCode': 500, 'body': json.dumps(error_payload)}
            
        # Define S3 Key
        s3_key = f"uploads/yt-dlp/{temp_id}/{title}.mp3"
        print(f"Uploading to S3: s3://{BUCKET_NAME}/{s3_key}")
        
        # Send upload status
        send_ws_message(apigw_client, connection_id, {
            'type': 'status', 
            'message': 'Extraction complete. Uploading to S3...'
        })
        
        # Upload the converted MP3 file to S3 with connectionId in metadata
        extra_args = {}
        if connection_id:
            extra_args = {'Metadata': {'connection-id': connection_id}}
            
        s3_client.upload_file(final_file, BUCKET_NAME, s3_key, ExtraArgs=extra_args)
        
        # Cleanup /tmp
        os.remove(final_file)
        
        # Generate a presigned download URL for the extracted audio
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': s3_key},
            ExpiresIn=3600
        )
        
        # Send success response to WebSocket
        success_payload = {
            'type': 'extraction_complete',
            'message': 'Audio successfully extracted and uploaded to S3',
            's3_bucket': BUCKET_NAME,
            's3_key': s3_key,
            'title': title,
            'downloadUrl': presigned_url
        }
        send_ws_message(apigw_client, connection_id, success_payload)
        
        return {
            'statusCode': 200,
            'body': json.dumps(success_payload)
        }
        
    except ValueError as ve:
        print(f"Validation Error: {str(ve)}")
        error_payload = {'type': 'error', 'message': str(ve)}
        send_ws_message(apigw_client, connection_id, error_payload)
        return {'statusCode': 400, 'body': json.dumps(error_payload)}
        
    except yt_dlp.utils.DownloadError as de:
        print(f"Download Error: {str(de)}")
        error_payload = {'type': 'error', 'message': 'Failed to download from URL. It may be invalid or unsupported.'}
        send_ws_message(apigw_client, connection_id, error_payload)
        return {'statusCode': 400, 'body': json.dumps(error_payload)}
        
    except Exception as e:
        print(f"Error processing audio: {str(e)}")
        error_payload = {'type': 'error', 'message': f"Internal server error: {str(e)}"}
        send_ws_message(apigw_client, connection_id, error_payload)
        return {'statusCode': 500, 'body': json.dumps(error_payload)}
