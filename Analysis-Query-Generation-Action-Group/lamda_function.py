import json
import base64
import boto3

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock = boto3.client("bedrock-runtime")

# Specify the AWS Bedrock model ID for Claude-3
model_id = "anthropic.claude-3-sonnet-20240229-v1:0"
#Specify the prompt for bedrock
prompt_text = "Please analyze this architecture diagram in order to create Infrastructure-As-Code. Please provide a structured summary of the diagram and ask necessary questions about missing configuration components, dependencies, or unclear connections that are not present in the diagram and that are required to create the IaC. DO NOT assume or ask additional unrelated questions"

def lambda_handler(event, context):
    print(event)
    # Get S3 bucket details from the event
    properties = {prop["name"]: prop["value"] for prop in event["requestBody"]["content"]["application/json"]["properties"]}
    bucket_name = properties['diagramS3Bucket']
    bucket_object = properties['diagramS3Key']
    try:
        # Fetch diagram from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=bucket_object)
        diagram_bytes = response['Body'].read()
    except Exception as e:
        print(f"Error fetching image from S3: {e}")
        return {'statusCode': 500, 'body': json.dumps(f"Error fetching image from S3: {e}")}

    # Encode the image in base64
    encoded_diagram = base64.b64encode(diagram_bytes).decode("utf-8")

    # Prepare the payload for invoking the Claude-3 model
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": encoded_diagram,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    })

    # Invoke the Claude-3 model
    try:
        response = bedrock.invoke_model(
            modelId=model_id, 
            body=body)
            
        # Decode response body from bytes to string and parse to JSON
        response_body = json.loads(response['body'].read().decode("utf-8"))
        print(response_body)
        
        return {
                'messageVersion': '1.0',
                'response': {
                    'actionGroup': event['actionGroup'],
                    'apiPath': event['apiPath'],
                    'httpMethod': event['httpMethod'],
                    'httpStatusCode': 200,
                    'responseBody': {
                        'application/json': {
                            'body': json.dumps({
                                "message": f"Summary and questions created are {response_body}"
                            })
                        }
                    },
                    'sessionAttributes': event.get('sessionAttributes', {}),
                    'promptSessionAttributes': event.get('promptSessionAttributes', {})
                }
        }
    except Exception as e:
        print(f"Error invoking Claude-3 model: {e}")
        return {'statusCode': 500, 'body': json.dumps(f"Error invoking Claude-3 model: {e}")}
