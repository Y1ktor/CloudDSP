import json
import boto3
import os

def lambda_handler(event, context):
    """
    Test Lambda to simulate AWS Batch completing a job.
    In the AWS Lambda Console, trigger this with a Test Event that looks like:
    {
      "connectionId": "YOUR_WEBSOCKET_CONNECTION_ID"
    }
    """
    # 1. Get the connection ID from the Lambda Test Event payload
    connection_id = event.get('connectionId')
    
    if not connection_id:
        return {'statusCode': 400, 'body': 'Error: Please provide {"connectionId": "..."} in the Test Event.'}
        
    s3_client = boto3.client('s3')
    
    # 2. Get configurations (Update these if necessary, or set them as Env Vars)
    ws_url = os.environ.get('WEBSOCKET_API_URL', 'https://grreq325rk.execute-api.us-east-1.amazonaws.com/dev')
    output_bucket = os.environ.get('OUTPUT_BUCKET', 'clouddsp-processed-audio-512383926199-us-east-1')
    
    apigw_client = boto3.client('apigatewaymanagementapi', endpoint_url=ws_url)
    
    # 3. Simulate the files in S3. 
    # UPDATE THESE KEYS to match the actual paths of the stem files you uploaded manually!
    stem_keys = {
        "vocals": "stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/vocals.wav",
        "drums": "stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/drums.wav",
        "bass": "stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/bass.wav",
        "other": "stems/96ae96a0-0e89-452e-87b6-5b0138b34273-Yosemite/other.wav"
    }
    
    # 4. Generate Pre-signed URLs
    print("Generating pre-signed download URLs...")
    download_urls = {}
    for stem_name, key in stem_keys.items():
        url = s3_client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': output_bucket, 'Key': key},
            ExpiresIn=3600
        )
        download_urls[stem_name] = url
        
    # 5. Construct WebSocket Payload
    payload = json.dumps({
        "type": "processing_complete",
        "stems": download_urls
    })
    
    # 6. Send directly to WebSocket API
    print(f"Sending completion event to WebSocket ID: {connection_id}")
    try:
        apigw_client.post_to_connection(
            ConnectionId=connection_id,
            Data=payload
        )
        print("Successfully notified frontend!")
        return {'statusCode': 200, 'body': 'Success'}
    except Exception as e:
        print(f"Failed to send WebSocket message: {e}")
        return {'statusCode': 500, 'body': str(e)}
