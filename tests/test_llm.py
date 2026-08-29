"""Extracting the model's answer from whatever shape the provider returns.

Only `response_text` is unit-tested here. It is the piece that fails quietly:
a provider returning content blocks instead of a string does not raise on
interpolation, it produces the repr of a list of dicts and passes it on as if
it were the model's answer.
"""

import pytest

from drawdownguard.llm import response_text


class _Message:
    def __init__(self, content):
        self.content = content


class _Block:
    def __init__(self, text):
        self.text = text


def test_a_plain_string_passes_through():
    assert response_text(_Message("OK")) == "OK"


def test_gemini_content_blocks_are_joined():
    """The real shape. Gemini answers with a list, not a string."""
    message = _Message(
        [
            {"type": "text", "text": "regime: ", "extras": {"signature": "..."}},
            {"type": "text", "text": "elevated"},
        ]
    )
    assert response_text(message) == "regime: elevated"


def test_non_text_blocks_are_dropped_not_stringified():
    """A reasoning signature is not the answer.

    Its repr is a plausible-looking base64 string, which is exactly what makes
    stringifying it dangerous: it survives a glance at a log.
    """
    message = _Message(
        [
            {"type": "reasoning", "signature": "ErgCCrUCARFNMg9b7yzBaZnsxMM"},
            {"type": "text", "text": "elevated"},
            {"type": "tool_call", "name": "search"},
        ]
    )
    assert response_text(message) == "elevated"


def test_object_blocks_are_handled_as_well_as_dicts():
    assert response_text(_Message([_Block("a"), _Block("b")])) == "ab"


def test_a_bare_string_is_accepted_without_a_message_wrapper():
    assert response_text("OK") == "OK"


def test_an_empty_response_is_empty_not_a_literal_empty_list():
    assert response_text(_Message([])) == ""


def test_the_llm_refuses_to_build_without_a_key(monkeypatch):
    """A missing key must fail here, by name, not inside a vendor library."""
    from drawdownguard import llm
    from drawdownguard.settings import Settings

    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: Settings(alpaca_api_key="a", alpaca_secret_key="b", google_api_key=""),
    )
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        llm.build_llm()
