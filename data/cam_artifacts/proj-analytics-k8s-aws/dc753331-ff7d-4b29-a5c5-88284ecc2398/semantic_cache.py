# CUI // SP-CTI
# Semantic cache using Bedrock embeddings + ElastiCache Redis.
# Reduces Bedrock API spend by caching semantically similar queries.
import hashlib
import json
import os
import boto3
import redis

BEDROCK = boto3.client("bedrock-runtime", region_name="us-east-1")
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
SIMILARITY_THRESHOLD = float(os.environ.get("SEMANTIC_CACHE_THRESHOLD", "0.92"))
TTL_SECONDS = int(os.environ.get("SEMANTIC_CACHE_TTL", "3600"))

_redis = redis.Redis(
    host=os.environ["REDIS_HOST"], port=6379,
    password=os.environ.get("REDIS_AUTH_TOKEN"),
    ssl=True, decode_responses=True,
)

def _embed(text: str) -> list[float]:
    resp = BEDROCK.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text}),
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0

def cached_invoke(prompt: str, invoke_fn) -> str:
    """Return cached response if semantically similar prompt exists."""
    emb = _embed(prompt)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:12]

    # Scan recent cache keys for semantic match
    cursor, keys = _redis.scan(cursor=0, match="semcache:*", count=200)
    for key in keys:
        cached = _redis.get(key)
        if not cached:
            continue
        entry = json.loads(cached)
        sim = _cosine_similarity(emb, entry["embedding"])
        if sim >= SIMILARITY_THRESHOLD:
            return entry["response"]

    # Cache miss — invoke and store
    response = invoke_fn(prompt)
    cache_key = f"semcache:{prompt_hash}"
    _redis.setex(cache_key, TTL_SECONDS, json.dumps({
        "prompt": prompt, "embedding": emb, "response": response,
    }))
    return response
