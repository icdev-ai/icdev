#!/usr/bin/env python3
"""DynamoDB table client wrappers for INTaaS.

Tables:
  intaas-topics       — Topics with linked sources (PK: TOPIC#{id})
  intaas-sources      — News sources + bias history (PK: SOURCE#{id})
  intaas-articles     — Ingested articles + bias scores (PK: ARTICLE#{id})
  intaas-perspectives — Side-by-side perspective views (PK: TOPIC#{id}, SK: PERSPECTIVE#{src})
  intaas-socratic     — Socratic dialogue sessions (PK: SESSION#{id}, SK: TURN#{n})
  intaas-annotations  — Collaborative annotations (PK: TOPIC#{id}, SK: ANNOTATION#{user}#{ts})
  intaas-judge-evals  — LLM-as-a-Judge results (PK: ARTICLE#{id}, SK: EVAL#{ts})
  intaas-tenants      — API key auth (PK: TENANT#{id})
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

from src import config

logger = logging.getLogger("intaas.db")

_dynamodb = boto3.resource("dynamodb")


def _topics_table():
    return _dynamodb.Table(config.TOPICS_TABLE)


def _sources_table():
    return _dynamodb.Table(config.SOURCES_TABLE)


def _articles_table():
    return _dynamodb.Table(config.ARTICLES_TABLE)


def _perspectives_table():
    return _dynamodb.Table(config.PERSPECTIVES_TABLE)


def _socratic_table():
    return _dynamodb.Table(config.SOCRATIC_TABLE)


def _annotations_table():
    return _dynamodb.Table(config.ANNOTATIONS_TABLE)


def _judge_table():
    return _dynamodb.Table(config.JUDGE_TABLE)


def _tenants_table():
    return _dynamodb.Table(config.TENANTS_TABLE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Tenants ──────────────────────────────────────────────────


def create_tenant(tenant_id: str, api_key: str, name: str = "", email: str = "") -> dict:
    item = {
        "PK": f"TENANT#{tenant_id}",
        "api_key_hash": hashlib.sha256(api_key.encode("utf-8")).hexdigest(),
        "name": name,
        "email": email,
        "created_at": _now(),
    }
    _tenants_table().put_item(Item=item)
    return item


# ── Topics ───────────────────────────────────────────────────


def create_topic(title: str, description: str = "", source_url: str = "") -> dict:
    topic_id = _short_id()
    item = {
        "PK": f"TOPIC#{topic_id}",
        "SK": "META",
        "topic_id": topic_id,
        "title": title,
        "description": description,
        "source_url": source_url,
        "status": "analyzing",
        "source_count": 0,
        "language_count": 0,
        "created_at": _now(),
    }
    _topics_table().put_item(Item=item)
    return item


def get_topic(topic_id: str) -> Optional[dict]:
    resp = _topics_table().get_item(Key={"PK": f"TOPIC#{topic_id}", "SK": "META"})
    return resp.get("Item")


def update_topic_status(topic_id: str, status: str, source_count: int = 0, language_count: int = 0):
    _topics_table().update_item(
        Key={"PK": f"TOPIC#{topic_id}", "SK": "META"},
        UpdateExpression="SET #s = :s, source_count = :sc, language_count = :lc, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status, ":sc": source_count, ":lc": language_count, ":u": _now(),
        },
    )


def list_topics(limit: int = 50) -> list[dict]:
    resp = _topics_table().scan(
        FilterExpression="SK = :meta",
        ExpressionAttributeValues={":meta": "META"},
        Limit=limit,
    )
    items = resp.get("Items", [])
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


# ── Articles ─────────────────────────────────────────────────


def store_article(
    topic_id: str,
    source_id: str,
    title: str,
    url: str,
    content: str,
    language: str = "en",
    published_at: str = "",
) -> dict:
    article_id = _short_id()
    item = {
        "PK": f"ARTICLE#{article_id}",
        "SK": "META",
        "article_id": article_id,
        "topic_id": topic_id,
        "source_id": source_id,
        "title": title,
        "url": url,
        "content": content[:10000],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        "language": language,
        "published_at": published_at or _now(),
        "created_at": _now(),
    }
    _articles_table().put_item(Item=item)
    return item


def get_article(article_id: str) -> Optional[dict]:
    resp = _articles_table().get_item(Key={"PK": f"ARTICLE#{article_id}", "SK": "META"})
    return resp.get("Item")


def get_articles_for_topic(topic_id: str) -> list[dict]:
    resp = _articles_table().scan(
        FilterExpression="topic_id = :tid AND SK = :meta",
        ExpressionAttributeValues={":tid": topic_id, ":meta": "META"},
    )
    return resp.get("Items", [])


# ── Bias Scores ──────────────────────────────────────────────


def store_bias_score(article_id: str, scores: dict[str, float], composite: float, color: str) -> dict:
    import json

    item = {
        "PK": f"ARTICLE#{article_id}",
        "SK": f"BIAS#{_now()}",
        "scores": json.dumps(scores),
        "composite": str(composite),
        "color": color,
        "created_at": _now(),
    }
    _articles_table().put_item(Item=item)
    return item


def get_bias_scores(article_id: str) -> list[dict]:
    resp = _articles_table().query(
        KeyConditionExpression=Key("PK").eq(f"ARTICLE#{article_id}")
        & Key("SK").begins_with("BIAS#"),
    )
    return resp.get("Items", [])


# ── Perspectives ─────────────────────────────────────────────


def store_perspective(
    topic_id: str,
    source_id: str,
    source_name: str,
    article_id: str,
    title: str,
    url: str,
    summary: str,
    bias_composite: float = 3.0,
    bias_color: str = "green",
    language: str = "en",
    title_en: str = "",
    summary_en: str = "",
) -> dict:
    item = {
        "PK": f"TOPIC#{topic_id}",
        "SK": f"PERSPECTIVE#{source_id}",
        "source_id": source_id,
        "source_name": source_name,
        "article_id": article_id,
        "title": title,
        "url": url,
        "summary": summary,
        "bias_composite": str(bias_composite),
        "bias_color": bias_color,
        "language": language,
        "created_at": _now(),
    }
    if title_en:
        item["title_en"] = title_en
    if summary_en:
        item["summary_en"] = summary_en
    _perspectives_table().put_item(Item=item)
    return item


def get_perspectives(topic_id: str) -> list[dict]:
    resp = _perspectives_table().query(
        KeyConditionExpression=Key("PK").eq(f"TOPIC#{topic_id}")
        & Key("SK").begins_with("PERSPECTIVE#"),
    )
    return resp.get("Items", [])


# ── Socratic Sessions ────────────────────────────────────────


def create_socratic_session(topic_id: str) -> dict:
    session_id = _short_id()
    item = {
        "PK": f"SESSION#{session_id}",
        "SK": "META",
        "session_id": session_id,
        "topic_id": topic_id,
        "turn_count": 0,
        "status": "active",
        "created_at": _now(),
    }
    _socratic_table().put_item(Item=item)
    return item


def add_socratic_turn(session_id: str, turn_number: int, role: str, content: str, question_type: str = "") -> dict:
    item = {
        "PK": f"SESSION#{session_id}",
        "SK": f"TURN#{turn_number:04d}",
        "turn_number": turn_number,
        "role": role,
        "content": content,
        "question_type": question_type,
        "created_at": _now(),
    }
    _socratic_table().put_item(Item=item)

    _socratic_table().update_item(
        Key={"PK": f"SESSION#{session_id}", "SK": "META"},
        UpdateExpression="SET turn_count = :tc",
        ExpressionAttributeValues={":tc": turn_number},
    )
    return item


def get_socratic_session(session_id: str) -> Optional[dict]:
    resp = _socratic_table().query(
        KeyConditionExpression=Key("PK").eq(f"SESSION#{session_id}"),
    )
    items = resp.get("Items", [])
    if not items:
        return None
    meta = next((i for i in items if i["SK"] == "META"), {})
    turns = sorted(
        [i for i in items if i["SK"].startswith("TURN#")],
        key=lambda x: x.get("turn_number", 0),
    )
    meta["turns"] = turns
    return meta


# ── Judge Evaluations ────────────────────────────────────────


def store_judge_eval(
    article_id: str,
    rubric: str,
    dimension_scores: dict,
    composite_score: float,
    color: str,
    strengths: list[str],
    weaknesses: list[str],
    overall_assessment: str,
    model_used: str = "",
    eval_ms: int = 0,
) -> dict:
    import json

    eval_id = _short_id()
    item = {
        "PK": f"ARTICLE#{article_id}",
        "SK": f"EVAL#{_now()}",
        "eval_id": eval_id,
        "rubric": rubric,
        "dimension_scores": json.dumps(dimension_scores),
        "composite_score": str(composite_score),
        "color": color,
        "strengths": json.dumps(strengths),
        "weaknesses": json.dumps(weaknesses),
        "overall_assessment": overall_assessment,
        "model_used": model_used,
        "eval_ms": eval_ms,
        "created_at": _now(),
    }
    _judge_table().put_item(Item=item)
    return item


def get_judge_evals(article_id: str) -> list[dict]:
    resp = _judge_table().query(
        KeyConditionExpression=Key("PK").eq(f"ARTICLE#{article_id}")
        & Key("SK").begins_with("EVAL#"),
    )
    return resp.get("Items", [])


# ── Annotations ──────────────────────────────────────────────


def store_annotation(topic_id: str, user_id: str, content: str, annotation_type: str = "observation") -> dict:
    item = {
        "PK": f"TOPIC#{topic_id}",
        "SK": f"ANNOTATION#{user_id}#{_now()}",
        "user_id": user_id,
        "content": content,
        "annotation_type": annotation_type,
        "created_at": _now(),
    }
    _annotations_table().put_item(Item=item)
    return item


def get_annotations(topic_id: str) -> list[dict]:
    resp = _annotations_table().query(
        KeyConditionExpression=Key("PK").eq(f"TOPIC#{topic_id}")
        & Key("SK").begins_with("ANNOTATION#"),
    )
    return resp.get("Items", [])


# ── Cached Analysis Results ───────────────────────────────────


def store_cached_result(topic_id: str, result_type: str, data: dict) -> dict:
    """Cache an analysis result (entities, graph, etc.) to avoid recomputation."""
    import json
    item = {
        "PK": f"TOPIC#{topic_id}",
        "SK": f"CACHE#{result_type}",
        "result_type": result_type,
        "data": json.dumps(data, default=str),
        "created_at": _now(),
    }
    _topics_table().put_item(Item=item)
    return item


def get_cached_result(topic_id: str, result_type: str) -> dict | None:
    """Get a cached analysis result. Returns None if not cached."""
    import json
    resp = _topics_table().get_item(
        Key={"PK": f"TOPIC#{topic_id}", "SK": f"CACHE#{result_type}"},
    )
    item = resp.get("Item")
    if not item:
        return None
    try:
        return json.loads(item.get("data", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None


def clear_cached_results(topic_id: str):
    """Clear all cached results for a topic (used on rescore)."""
    for result_type in ["entities", "graph_analysis"]:
        try:
            _topics_table().delete_item(
                Key={"PK": f"TOPIC#{topic_id}", "SK": f"CACHE#{result_type}"},
            )
        except Exception:
            pass


# ── Sources ──────────────────────────────────────────────────


def register_source(name: str, source_type: str, url: str = "", bias_profile: str = "unknown") -> dict:
    source_id = _short_id()
    item = {
        "PK": f"SOURCE#{source_id}",
        "SK": "META",
        "source_id": source_id,
        "name": name,
        "source_type": source_type,
        "url": url,
        "bias_profile": bias_profile,
        "article_count": 0,
        "created_at": _now(),
    }
    _sources_table().put_item(Item=item)
    return item


def list_sources(limit: int = 100) -> list[dict]:
    resp = _sources_table().scan(
        FilterExpression="SK = :meta",
        ExpressionAttributeValues={":meta": "META"},
        Limit=limit,
    )
    return resp.get("Items", [])


# ── DynamoDB Table Definitions (for moto/local setup) ────────

TABLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "topics": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "sources": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "articles": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "perspectives": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "socratic": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "annotations": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "judge_evals": {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
    },
    "tenants": {
        "KeySchema": [{"AttributeName": "PK", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "PK", "AttributeType": "S"}],
    },
}
