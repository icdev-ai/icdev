# CUI // SP-CTI
# Bedrock Knowledge Base + OpenSearch vector store setup.
# Run after OpenSearch restore and index template migration.
import boto3

bedrock_agent = boto3.client("bedrock-agent", region_name="us-east-1")
OPENSEARCH_HOST = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"

def create_knowledge_base():
    resp = bedrock_agent.create_knowledge_base(
        name="analytics-platform-kb",
        description="Knowledge base for analytics platform semantic search",
        roleArn="arn:aws:iam::ACCOUNT_ID:role/BedrockKBRole",
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
            },
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": "arn:aws:aoss:us-east-1:ACCOUNT_ID:collection/COLLECTION_ID",
                "vectorIndexName": "analytics-index",
                "fieldMapping": {
                    "vectorField": "embedding", "textField": "text",
                    "metadataField": "metadata",
                },
            },
        },
    )
    kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
    print(f"Knowledge base created: {kb_id}")
    return kb_id

def rag_query(kb_id: str, query: str, max_results: int = 5) -> str:
    """Retrieve-and-generate using the knowledge base."""
    bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    resp = bedrock_runtime.retrieve_and_generate(
        input={"text": query},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": kb_id,
                "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0",
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": max_results}
                },
            },
        },
    )
    return resp["output"]["text"]

if __name__ == "__main__":
    kb_id = create_knowledge_base()
    # Test query
    answer = rag_query(kb_id, "What are the top metrics for Q1?")
    print("RAG answer:", answer)
