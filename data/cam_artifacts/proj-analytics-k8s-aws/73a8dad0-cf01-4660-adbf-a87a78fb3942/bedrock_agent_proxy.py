# CUI // SP-CTI
# Flask proxy endpoint: React → Bedrock InlineAgent (streaming)
# Add to your Flask app to avoid exposing AWS credentials to the browser.
import boto3
from flask import Blueprint, request, Response, stream_with_context

bedrock_bp = Blueprint("bedrock", __name__)
bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name="us-east-1")

@bedrock_bp.route("/api/bedrock-agent", methods=["POST"])
def invoke_agent():
    body = request.get_json(force=True)
    agent_id       = body.get("agentId")
    agent_alias_id = body.get("agentAliasId")
    session_id     = body.get("sessionId")
    input_text     = body.get("inputText", "")

    def generate():
        resp = bedrock_runtime.invoke_agent(
            agentId=agent_id,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            inputText=input_text,
        )
        for event in resp["completion"]:
            if "chunk" in event:
                yield event["chunk"]["bytes"].decode("utf-8")

    return Response(stream_with_context(generate()), content_type="text/plain")
