# CUI // SP-CTI
# Backfill embeddings for existing rows using Amazon Bedrock Titan Embed.
# Run once after DMS full-load, before HNSW index creation.
import boto3
import json
import psycopg2

BEDROCK = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL_ID = "amazon.titan-embed-text-v2:0"
BATCH_SIZE = 100

def get_embedding(text: str) -> list[float]:
    resp = BEDROCK.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({"inputText": text}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]

def backfill(conn_str: str, text_column: str = "name", table: str = "example_entity"):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    cur.execute(f"SELECT id, {text_column} FROM {table} WHERE embedding IS NULL")
    rows = cur.fetchmany(BATCH_SIZE)
    while rows:
        for row_id, text in rows:
            if text:
                emb = get_embedding(text)
                cur.execute(
                    f"UPDATE {table} SET embedding = %s::vector WHERE id = %s",
                    (str(emb), row_id),
                )
        conn.commit()
        rows = cur.fetchmany(BATCH_SIZE)
    conn.close()
    print("Backfill complete.")

if __name__ == "__main__":
    import os
    backfill(os.environ["AURORA_CONN_STR"])
