import os
import json
import boto3
import uuid
import yt_dlp
from botocore.exceptions import ClientError

s3_client = boto3.client('s3')

# We can retrieve the target bucket from an environment variable
BUCKET_NAME = os.environ.get('UPLOAD_BUCKET', 'clouddsp-uploads-bucket')

def lambda_handler(event, context):
    """
    AWS Lambda function to extract audio from a given URL using yt-dlp and ffmpeg,
    and save the output MP3 file to S3.
    """
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
            
        url = body.get('url')
        
        if not url:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Missing required parameter: url'})
            }
            
        print(f"Processing URL: {url}")
        
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
             return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Failed to extract and convert audio to MP3'})
            }
            
        # Define S3 Key
        s3_key = f"uploads/yt-dlp/{temp_id}/{title}.mp3"
        print(f"Uploading to S3: s3://{BUCKET_NAME}/{s3_key}")
        
        # Upload the converted MP3 file to S3
        s3_client.upload_file(final_file, BUCKET_NAME, s3_key)
        
        # Cleanup /tmp
        os.remove(final_file)
        
        # Return success response
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': 'Audio successfully extracted and uploaded to S3',
                's3_bucket': BUCKET_NAME,
                's3_key': s3_key,
                'title': title
            })
        }
        
    except yt_dlp.utils.DownloadError as de:
        print(f"Download Error: {str(de)}")
        return {
            'statusCode': 400,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': f"Failed to download from URL. It may be invalid or unsupported."})
        }
    except ValueError as ve:
        print(f"Validation Error: {str(ve)}")
        return {
            'statusCode': 400,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(ve)})
        }
    except Exception as e:
        print(f"Error processing audio: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': f"Internal server error: {str(e)}"})
        }
