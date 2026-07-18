import json
import os
import boto3
import uuid

# Initialize the S3 client outside the handler
s3_client = boto3.client('s3')

# Pull the bucket name from the environment variable (set by CloudFormation)
BUCKET_NAME = os.environ.get('INCOMING_BUCKET_NAME')

def lambda_handler(event, context):

    # Parse query parameters from frontend request
    query_params = event.get('queryStringParameters', {}) or {}
    file_name = query_params.get('filename')
    file_type = query_params.get('filetype') # e.g., audio/wav, audio/mpeg
    connection_id = query_params.get('connectionId', 'unknown')
    stem_mode = query_params.get('stemMode', '6-stems')

    try:

        # Ensure we have a valid filename and construct the key
        safe_file_name = file_name if file_name else "unnamed_file"
        file_key = f"uploads/local/{uuid.uuid4()}-{safe_file_name}"
        
        # Use provided file type or fallback to a generic binary stream
        content_type = file_type if file_type else 'application/octet-stream'

        # Generate the pre-signed URL for a PUT request
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod='put_object',
            Params={
                'Bucket': BUCKET_NAME, # Using the Environment Variable
                'Key': file_key,
                'ContentType': content_type,
                'Metadata': {
                    'connection-id': connection_id,
                    'stem-mode': stem_mode
                }
            },
            ExpiresIn=300 # URL expires in 5 minutes
        )
        
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                "Access-Control-Allow-Methods": "GET,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                'uploadUrl': presigned_url,
                'key': file_key
            })
        }
    except Exception as e:
        print(f"Error generating pre-signed URL: {e}")
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                "Access-Control-Allow-Methods": "GET,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                'Content-Type': 'application/json'
            },
            'body': json.dumps({'error': 'Failed to generate upload URL'})
        }
