# Auto-grader for M-T1-11 Step 2: Image-in-Prompt document classifier
#
# run_code executes the learner's script and then this file in ONE namespace, so
# the learner's DocumentClassifier is visible through globals(). The vision call is
# replaced with a recording fake whose replies carry values chosen here at random,
# so a classifier that hardcodes its answers cannot pass.

import base64 as _b64
import json as _json
import random as _random
import uuid as _uuid

_Classifier = globals().get("DocumentClassifier")
_Result = globals().get("ClassificationResult")
_real_call = globals().get("simulate_vision_call")
assert isinstance(_Classifier, type), (
    "DocumentClassifier is not defined — keep the starter's class and fill in its methods."
)
assert isinstance(_Result, type), "ClassificationResult is not defined — keep the starter's dataclass."
assert callable(_real_call), "simulate_vision_call is not defined — keep the starter's stub."

_CATEGORIES = ["Government form", "Technical diagram", "Scanned report", "Other"]
_calls = []
_reply = {"text": ""}


def _fake_vision_call(messages):
    _calls.append(messages)
    return {"model": "grader", "content": [{"type": "text", "text": _reply["text"]}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


globals()["simulate_vision_call"] = _fake_vision_call


def _classify(image_input, text, threshold=0.75):
    _reply["text"] = text
    _calls.clear()
    clf = _Classifier(categories=list(_CATEGORIES), confidence_threshold=threshold)
    result = clf.classify(image_input)
    assert isinstance(result, _Result), (
        f"classify() must return a ClassificationResult, got {type(result).__name__}"
    )
    return result


_nonce = _uuid.uuid4().hex[:8]
_doc = b"\x89PNG fake scan " + _nonce.encode()

# --- 1. _encode_image: bytes and file paths --------------------------------------
_clf = _Classifier(categories=list(_CATEGORIES), confidence_threshold=0.75)
_enc = _clf._encode_image(_doc)
assert isinstance(_enc, tuple) and len(_enc) == 2, "_encode_image must return (base64_text, media_type)."
assert _enc[1] == "image/png", f"bytes input should be media type 'image/png', got {_enc[1]!r}"
assert isinstance(_enc[0], str) and _b64.b64decode(_enc[0]) == _doc, (
    "_encode_image did not base64-encode the image bytes (decode with "
    "base64.standard_b64encode(data).decode('utf-8'))."
)
for _ext, _mt in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".png", "image/png")):
    _name = f"scan_{_nonce}{_ext}"
    with open(_name, "wb") as _f:
        _f.write(_doc)
    _enc = _clf._encode_image(_name)
    assert _enc[1] == _mt, f"a {_ext} path should be media type {_mt!r}, got {_enc[1]!r}"
    assert _b64.b64decode(_enc[0]) == _doc, f"_encode_image did not read the bytes of the {_ext} file."

# --- 2. classify sends the image, then parses a confident reply -------------------
_cat = _random.choice(_CATEGORIES)
_conf = round(_random.uniform(0.80, 0.97), 2)
_why = f"grader reason {_nonce}"
_r = _classify(_doc, _json.dumps({"category": _cat, "confidence": _conf, "reason": _why}))
assert len(_calls) == 1, f"classify() should make exactly one vision call, made {len(_calls)}."
_msgs = _calls[0]
assert isinstance(_msgs, list) and len(_msgs) == 1 and _msgs[0].get("role") == "user", (
    "The vision call takes ONE user message: [{'role': 'user', 'content': [...]}]."
)
_blocks = _msgs[0].get("content")
assert isinstance(_blocks, list) and len(_blocks) == 2, (
    "The user message content must be a list of two blocks: the image, then the text prompt."
)
assert _blocks[0].get("type") == "image", "The first content block must be the image block."
_src = _blocks[0].get("source") or {}
assert _src.get("type") == "base64" and _src.get("media_type") == "image/png", (
    "The image block's source must be {'type': 'base64', 'media_type': ..., 'data': ...}."
)
assert _b64.b64decode(_src.get("data", "")) == _doc, "The image block does not carry the image you were given."
assert _blocks[1].get("type") == "text" and all(c in _blocks[1].get("text", "") for c in _CATEGORIES), (
    "The second block must be the text prompt, and it must list every category."
)
assert (_r.category, _r.reason) == (_cat, _why), (
    f"category/reason must come from the model's reply — expected {(_cat, _why)}, "
    f"got {(_r.category, _r.reason)}"
)
assert abs(float(_r.confidence) - _conf) < 1e-9, f"confidence should be {_conf}, got {_r.confidence!r}"
assert _r.accepted is True, f"confidence {_conf} meets the 0.75 threshold, so accepted must be True."

# --- 3. The threshold actually gates acceptance ----------------------------------
_low = round(_random.uniform(0.30, 0.60), 2)
_r = _classify(_doc, _json.dumps({"category": "Other", "confidence": _low, "reason": "faint"}))
assert _r.accepted is False, f"confidence {_low} is below 0.75 — accepted must be False."
_r = _classify(_doc, _json.dumps({"category": "Other", "confidence": 0.5, "reason": "edge"}), threshold=0.5)
assert _r.accepted is True, "confidence equal to the threshold is accepted (confidence >= threshold)."

# --- 4. Fenced JSON is unwrapped -------------------------------------------------
_fconf = round(_random.uniform(0.76, 0.99), 2)
_body = _json.dumps({"category": "Scanned report", "confidence": _fconf, "reason": _why})
for _fenced in ("```json\n" + _body + "\n```", "```\n" + _body + "\n```"):
    _r = _classify(_doc, _fenced)
    assert _r.category == "Scanned report" and abs(float(_r.confidence) - _fconf) < 1e-9, (
        "A reply wrapped in ``` fences (with or without 'json') must still be parsed."
    )

# --- 5. A reply that is not JSON degrades, it does not crash ---------------------
try:
    _r = _classify(_doc, "Sorry, I cannot read this document.")
except Exception as _exc:
    raise AssertionError(
        f"classify() raised {type(_exc).__name__} on a non-JSON reply — catch it and "
        "return category='unknown', confidence=0.0, accepted=False."
    )
assert _r.category == "unknown" and float(_r.confidence) == 0.0 and _r.accepted is False, (
    "On a non-JSON reply return ClassificationResult('unknown', 0.0, <reason>, False)."
)

# --- 6. A file path works end to end ---------------------------------------------
_r = _classify(f"scan_{_nonce}.jpg", _json.dumps({"category": _cat, "confidence": _conf, "reason": _why}))
assert _calls and _calls[0][0]["content"][0]["source"]["media_type"] == "image/jpeg", (
    "Classifying a .jpg path must send media type 'image/jpeg'."
)
assert _r.category == _cat, "Classifying a file path must return the model's category."

# --- 7. And against the starter's simulator, unmodified ---------------------------
globals()["simulate_vision_call"] = _real_call
_reply["text"] = ""
_r = _Classifier(categories=list(_CATEGORIES), confidence_threshold=0.75).classify(
    b"\x89PNG ... STANDARD FORM 86 ..."
)
assert (_r.category, _r.accepted) == ("Government form", True), (
    f"Against simulate_vision_call an SF-86 scan should be an accepted 'Government form', got {_r}"
)

print("PASS: your classifier sends the image, parses the reply, and gates on confidence.")
