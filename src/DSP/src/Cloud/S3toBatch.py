import json
import boto3
import os
import urllib.parse
import uuid

def lambda_handler(event, context):
    """
    AWS Lambda function that triggers an AWS Batch Demucs job when a file is uploaded to S3.
    This acts as the bridge between the S3 upload event and the Docker container.
    """
    batch_client = boto3.client('batch')
    s3_client = boto3.client('s3')
    
    # These must be set in the Lambda's Environment Variables in the AWS Console
    job_queue = os.environ.get('BATCH_JOB_QUEUE')
    job_definition = os.environ.get('BATCH_JOB_DEFINITION')
    output_bucket = os.environ.get('OUTPUT_BUCKET')
    
    if not job_queue or not job_definition:
        print("Error: Missing BATCH_JOB_QUEUE or BATCH_JOB_DEFINITION environment variables.")
        return {'statusCode': 500, 'body': 'Configuration error.'}

    try:
        for record in event.get('Records', []):
            # Handle standard S3 Event Notification
            if 's3' in record:
                bucket = record['s3']['bucket']['name']
                key = urllib.parse.unquote_plus(record['s3']['object']['key'])
                
                if not output_bucket:
                    output_bucket = bucket
                    
                # 1. Dynamically retrieve the Stem Mode and Connection ID from S3 Object Metadata!
                # When the frontend requests a pre-signed URL, it attaches this metadata.
                stem_mode = "6-stems" # Default fallback
                connection_id = ""
                try:
                    head_response = s3_client.head_object(Bucket=bucket, Key=key)
                    # S3 metadata keys are returned automatically lowercased
                    if 'Metadata' in head_response:
                        if 'stem-mode' in head_response['Metadata']:
                            stem_mode = head_response['Metadata']['stem-mode']
                            print(f"Found stem-mode metadata attached to file: {stem_mode}")
                        if 'connection-id' in head_response['Metadata']:
                            connection_id = head_response['Metadata']['connection-id']
                            print(f"Found WebSocket connection ID attached to file.")
                except Exception as meta_e:
                    print(f"Could not retrieve metadata for {key}. Error: {meta_e}")

                # 2. Generate a clean, unique job name for the AWS Batch Console
                safe_key = key.split('/')[-1].replace('.', '_').replace('-', '_')[:30]
                job_name = f"demucs_{safe_key}_{uuid.uuid4().hex[:6]}"
                
                print(f"Submitting Batch job for s3://{bucket}/{key} with mode: {stem_mode}")
                
                # 3. Submit the Job to AWS Batch
                # We use containerOverrides to pass the specific file details into the Docker container
                response = batch_client.submit_job(
                    jobName=job_name,
                    jobQueue=job_queue,
                    jobDefinition=job_definition,
                    containerOverrides={
                        'environment': [
                            {'name': 'INPUT_BUCKET', 'value': bucket},
                            {'name': 'OUTPUT_BUCKET', 'value': output_bucket},
                            {'name': 'FILE_KEY', 'value': key},
                            {'name': 'STEM_MODE', 'value': stem_mode},
                            {'name': 'CONNECTION_ID', 'value': connection_id} 
                        ]
                    }
                )
                
                print(f"Successfully submitted job {response['jobId']} to AWS Batch.")
                
        return {
            'statusCode': 200,
            'body': json.dumps('Successfully submitted AWS Batch job(s).')
        }
    except Exception as e:
        print(f"Error submitting batch job: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f"Error: {str(e)}")
        }
