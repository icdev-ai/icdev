#!/usr/bin/env python3
"""Translation service — translates non-English article titles and summaries to English.

Uses Claude Haiku via Anthropic API (same key as LLM judge).
Falls back to original text if translation unavailable.
Caches translations in memory to avoid duplicate API calls.
"""
import logging

import requests

from src import config

logger = logging.getLogger("intaas.services.translation")

_cache: dict[str, str] = {}  # hash(text) → translated text


def needs_translation(text: str, language: str = "") -> bool:
    """Check if text needs translation based on language or character detection."""
    if language and language.lower() in ("english", "en"):
        return False

    # Quick ASCII check — if mostly ASCII, probably English
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ratio = non_ascii / max(len(text), 1)
    return ratio > 0.3


def translate_to_english(text: str, source_language: str = "") -> str:
    """Translate text to English using Claude Haiku.

    Returns original text if:
      - Already English
      - LLM not enabled
      - Translation fails
    """
    if not text or not needs_translation(text, source_language):
        return text

    # Check cache
    cache_key = str(hash(text[:200]))
    if cache_key in _cache:
        return _cache[cache_key]

    if not config.LLM_JUDGE_ENABLED or not config.ANTHROPIC_API_KEY:
        return text

    try:
        lang_hint = f" (source language: {source_language})" if source_language else ""
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_JUDGE_MODEL,
                "max_tokens": 512,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Translate the following text to English{lang_hint}. "
                        "Return ONLY the English translation, nothing else.\n\n"
                        f"{text[:1000]}"
                    ),
                }],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        translated = data.get("content", [{}])[0].get("text", "").strip()
        if translated and len(translated) > 5:
            _cache[cache_key] = translated
            return translated
    except Exception as e:
        logger.warning("Translation failed: %s", e)

    return text


def translate_perspective(perspective: dict) -> dict:
    """Translate a perspective's title and summary if non-English."""
    language = perspective.get("language", "English")
    if not needs_translation(perspective.get("title", ""), language):
        return perspective

    result = dict(perspective)
    title = perspective.get("title", "")
    summary = perspective.get("summary", "")

    translated_title = translate_to_english(title, language)
    if translated_title != title:
        result["title_original"] = title
        result["title"] = translated_title

    translated_summary = translate_to_english(summary, language)
    if translated_summary != summary:
        result["summary_original"] = summary
        result["summary"] = translated_summary

    result["translated"] = translated_title != title or translated_summary != summary
    return result
