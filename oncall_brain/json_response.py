"""Robust decoding of a JSON value out of a chat-model response.

Providers regularly wrap the JSON in a Markdown fence even when the request
asked for `response_format={"type":"json_object"}`, and some prefix or suffix
it with a sentence. Every model output in this project is decoded through
`parse_model_json` so one wrapper cannot take the whole ingest down.
"""
from __future__ import annotations

import json
import re

# One complete fenced block, with or without a language tag, anywhere in the
# text: "```json\n{...}\n```", "```\n{...}\n```", "Sure:\n```json\n{...}\n```".
_FENCE_RE = re.compile(
    r"```(?:[A-Za-z0-9_+.-]*)[ \t]*\r?\n(.*?)\r?\n?[ \t]*```", re.S)


def parse_model_json(text: str):
    """Decode the JSON value in a model response; raise if there is none.

    Order: a fenced block if present, then the whole text as-is, then the
    outermost `{...}` when prose surrounds the JSON. A response that holds no
    JSON at all still raises `json.JSONDecodeError`, so callers can tell a
    malformed answer from an empty one.
    """
    s = (text or "").strip()
    if not s:
        raise json.JSONDecodeError("empty model response", s, 0)
    fenced = _FENCE_RE.search(s)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass  # the fence wrapped something else; try the whole text
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        return json.loads(s[start:end + 1])
    raise json.JSONDecodeError("no JSON object in model response", s, 0)


def object_from_response(content: str) -> dict:
    """Decode a JSON *object* from a model response (fences tolerated)."""
    data = parse_model_json(content)
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    return data
