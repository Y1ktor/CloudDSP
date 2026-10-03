import json
import boto3
import os

def lambda_handler(event, context):
    """
    AWS API Gateway WebSocket Handler.
    This Lambda function captures the unique Connection ID from the AWS request context
    and immediately echoes it back to the React frontend.
    
    IMPORTANT ARCHITECTURE NOTE:
    Do NOT attach this Lambda to the `$connect` route. 
    In AWS API Gateway, a WebSocket connection is not officially "open" until the `$connect` 
    Lambda finishes running and returns a 200 OK. If you try to `post_to_connection` inside 
    the `$connect` route, AWS will throw a "410 Gone" error because the client isn't fully registered yet.
    
    Instead, attach this Lambda to the `$default` route. 
    React will connect, the socket will open, and then React will immediately send a tiny 
    dummy message (like {"action": "echo"}) which triggers this `$default` Lambda safely!
    """
    
    # 1. Extract the Connection ID from the AWS Event
    request_context = event.get('requestContext', {})
    connection_id = request_context.get('connectionId')
    
    # We can dynamically determine the WebSocket URL directly from the event!
    domain_name = request_context.get('domainName')
    stage = request_context.get('stage')
    
    if not connection_id or not domain_name:
        return {'statusCode': 400, 'body': 'Missing connection context.'}
        
    # 2. Initialize the API Gateway Management Client
    # Note the 'https://' prefix. We push HTTP POSTs to API Gateway, and AWS turns them into WebSocket messages.
    endpoint_url = f"https://{domain_name}/{stage}"
    apigw_client = boto3.client('apigatewaymanagementapi', endpoint_url=endpoint_url)
    
    # 3. Construct the JSON payload for React
    payload = json.dumps({
        "type": "connected",
        "connectionId": connection_id
    })
    
    # 4. Push the payload directly down the WebSocket to the browser
    try:
        print(f"Echoing Connection ID: {connection_id} back to client.")
        apigw_client.post_to_connection(
            ConnectionId=connection_id,
            Data=payload
        )
    except Exception as e:
        print(f"Failed to post to connection {connection_id}. Error: {e}")
        # We still return 200 so the connection stays alive
        return {'statusCode': 200, 'body': 'Connection failed to echo.'}
        
    # 5. Return 200 OK to API Gateway to acknowledge successful processing
    return {
        'statusCode': 200,
        'body': 'Connection ID Echoed Successfully.'
    }
