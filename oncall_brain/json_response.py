"""Decode a JSON object, optionally wrapped in one complete Markdown fence."""
import json
import re


def object_from_response(content: str) -> dict:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    data = json.loads(fenced.group(1) if fenced else text)
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    return data
