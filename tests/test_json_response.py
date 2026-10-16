"""Model output decoding: providers wrap JSON despite json_object mode."""
from __future__ import annotations

import json

import pytest

from oncall_brain.json_response import object_from_response, parse_model_json

PAYLOAD = {"assertions": [{"subject": "A", "object": "B"}]}
RAW = json.dumps(PAYLOAD)


@pytest.mark.parametrize("response", [
    RAW,                                    # bare JSON
    f"```json\n{RAW}\n```",                 # fenced, language tag
    f"```\n{RAW}\n```",                     # fenced, no language
    f"```json\n{RAW}\n```",                 # fenced again (defensive)
    f"Here is the result:\n```json\n{RAW}\n```\nLet me know if you need more.",
    f"Sure! {RAW}\nHope that helps.",       # prose around bare JSON
    f"  \n\n{RAW}\n\n",                     # surrounding whitespace
], ids=["bare", "fenced-json", "fenced-plain", "fenced-json-dup",
        "prose-around-fence", "prose-around-json", "whitespace"])
def test_decodes_wrapped_json(response):
    assert parse_model_json(response) == PAYLOAD


def test_non_json_still_raises():
    for response in ("", "I cannot help with that.", "no json here: 1 2 3",
                     "```json\nnot json at all\n```", "{'single': 'quotes'}"):
        with pytest.raises(json.JSONDecodeError):
            parse_model_json(response)


def test_empty_string_is_an_error_not_a_silent_default():
    with pytest.raises(json.JSONDecodeError):
        parse_model_json("")


def test_object_from_response_accepts_a_fenced_object():
    assert object_from_response(f"```json\n{RAW}\n```") == PAYLOAD


def test_object_from_response_rejects_a_non_object():
    with pytest.raises(ValueError):
        object_from_response("[1, 2, 3]")
