import json
import logging
import base64
import os
import boto3
from botocore.exceptions import ClientError
import requests

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize a Boto3 client for S3
s3_client = boto3.client('s3')

# Initialize a Boto3 client for Bedrock
bedrock = boto3.client(service_name='bedrock-runtime')
bedrock_client = boto3.client(service_name='bedrock-agent-runtime')
model_id = "anthropic.claude-3-sonnet-20240229-v1:0"

def invoke_bedrock_model(prompt, bucket_name, bucket_object, final_draft):
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
        "temperature": 0,
        "top_k": 250,
        "top_p": 1,
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
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    })

    # Invoke the Claude-3 model
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            body=body,
            accept="*/*",
            contentType="application/json"
        )
        
        # Decode response body from bytes to string and parse to JSON
        response_body = json.loads(response.get('body').read())
        # Return generated Terraform code
        tf_code = response_body['content'][0]['text']
        return tf_code

    except Exception as e:
        print(f"Error invoking Claude-3 model")
    return {'statusCode': 500, 'body': json.dumps(f"Error invoking Claude-3 model")}  

def create_and_commit_file(repo_owner, repo_name, path, token, commit_message, content):
    # Construct the URL for GitHub API
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{path}'
    
    # Headers for GitHub API
    headers = {
        "Authorization": f'token {token}',
        "Accept": 'application/json'
    }
    
    # Check if the file exists and get its sha (necessary for updating the file)
    get_response = requests.get(url, headers=headers, timeout=30)
    sha = None
    if get_response.status_code == 200:
        sha = get_response.json()['sha']
    elif get_response.status_code != 404:
        get_response.raise_for_status()
        
    # Encode the content to base64
    encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    
    # Prepare the payload for the GitHub API request
    data = {
        'message': commit_message,
        'content': encoded_content,
        'sha': sha  
    }
    if sha:
        data['sha'] = sha
    
    # Make the PUT request to GitHub API to create the file
    response = requests.put(url, headers=headers, data=json.dumps(data), timeout=30)
    
    # Check the response from GitHub
    if response.status_code in [200, 201]:
        logger.info(f'{path} successfully created/updated in GitHub repo.')
    else:
        logger.error(f'Failed to create/update {path}', response.json())
        response.raise_for_status()
 
def retrieve_module_definitions(knowledge_base_id, model_arn):
    query_text = f"Retrieve Terraform module sources for AWS services"
    try:
        response = bedrock_client.retrieve_and_generate(
            input={
                'text': query_text
            },
            retrieveAndGenerateConfiguration={
                'type': 'KNOWLEDGE_BASE',
                'knowledgeBaseConfiguration': {
                    'knowledgeBaseId': knowledge_base_id,
                    'modelArn': model_arn
                }
            }
        )
        
    # Extracting the text from the response
        print("KB Response 1:", response) 
        response_text = response['output']['text']
        print("KB Response:", response_text)  # Print the response text

        # Assuming the response text contains a JSON string with module definitions
        module_definitions = response_text #json.loads(response_text)
        return module_definitions

    except ClientError as e:
        print("An error occurred:", e)
        return {}
    except json.JSONDecodeError as json_err:
        print("JSON parsing error:", json_err)
        return {}

def lambda_handler(event, context):
    # Print the entire event
    print("Received event: " + json.dumps(event))
    try:
        properties = {prop["name"]: prop["value"] for prop in event["requestBody"]["content"]["application/json"]["properties"]}
        bucket_name = properties['diagramS3Bucket']
        bucket_object = properties['diagramS3Key']
        final_draft = properties['final_draft'] 

        # GitHub information
        repo_owner = 'input-repo-owner-name'
        repo_name = 'input-repo-name'
        token = os.environ['GITHUB_TOKEN']
        account_email = 'input-email'
        commit_message = 'Initial terraform code'

        #Knowledge base ID
        kb_id = os.environ['KNOWLEDGE_BASE_ID']

        # Define the directory path and file names
        main_tf_path = f'test/main.tf'
        main_tf_path_url = f'https://github.com/{repo_owner}/{repo_name}/blob/main/{main_tf_path}'

        # Generate Terraform config using Bedrock model
        module_definitions = retrieve_module_definitions(kb_id, "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-v2")

        # Construct the prompt with module definitions
        terraform_prompt = f"Please analyze the architecture diagram in order to create Infrastrucute-As-A-code. Please use {final_draft} and create the necessary IaC in Terraform: "
        #For CloudFormation : terraform_prompt = f"Please analyze the architecture diagram in order to create Infrastrucute-As-A-code. Please use {final_draft} and create the necessary IaC in CloudFormation: "
        terraform_prompt += ". Use the following module definitions whereever applicable: "
        terraform_prompt += json.dumps(module_definitions)
        terraform_prompt += " Give only Terraform code as the output response."
        #For CloudFormation : terraform_prompt += " Give only CloudFormation code as the output response."
        print("terraform_prompt", terraform_prompt)  # Print the response text

        # Invoke the model or method to generate Terraform configuration based on the prompt
        main_tf_content = invoke_bedrock_model(terraform_prompt, bucket_name, bucket_object, final_draft)
        # Commit main.tf to GitHub
        create_and_commit_file(repo_owner, repo_name, main_tf_path, token, f'{commit_message}', main_tf_content)
    
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
                            "message": f"Terraform code updated successfully",
                            "main_tf_path": main_tf_path_url
                        })
                    }
                },
                'sessionAttributes': event.get('sessionAttributes', {}),
                'promptSessionAttributes': event.get('promptSessionAttributes', {})
            }
        }

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        # Ensure that error responses also align with the OpenAPI schema
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps({
                "error": "An error occurred during the process.",
                "details": str(e)
            })
        }
