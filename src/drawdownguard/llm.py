"""The chat model, and the one place in this program that talks to one.

It is called once per cycle, in `journal`, to write the paragraph a client
reads. Everything it describes is already settled -- the strike is chosen, the
gate has ruled, the orders are at the broker and their fills have been read
back -- so which model produces that prose is a cost decision and not a safety
one. This project runs Gemini because that is the key its author has.

This module used to be `analyst/llm.py`, named for a role that read the market
and returned a judgement. That role was removed when it turned out nothing
imported it. The plumbing outlived it because the explanation still needs a
model, and it is named for what it is now.

WHY `response_text` EXISTS
--------------------------
Gemini returns `content` as a **list of content blocks**, not a string. Code
written against a provider that returns a plain string — `msg.content.strip()`,
`json.loads(msg.content)` — does not raise on the list. `.strip()` raises, but
`f"{msg.content}"` happily produces the repr of a list of dicts, complete with
base64 reasoning signatures, and passes it downstream as if it were the
model's answer.

So the extraction is a named function with a test, rather than an attribute
access repeated at each call site.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel

from drawdownguard.settings import get_settings

# Gemini 3.7 Flash: fast, cheap, and enough to write one paragraph from facts
# it is handed. One call per cycle, so the ceiling on spend is a few cents a
# day; there is nothing to be gained by going smaller.
DEFAULT_MODEL = "gemini-3.7-flash"

# The same numbers should produce the same sentence. Sampling variance buys
# nothing when the job is describing a decision already made, and it makes a
# disagreement between two runs impossible to attribute, so it is pinned off.
DEFAULT_TEMPERATURE = 0.0


def build_llm(
    model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE
) -> BaseChatModel:
    """The chat model, keyed from settings rather than the ambient env.

    Reading the key through `get_settings` rather than letting the client pick
    up `GOOGLE_API_KEY` on its own keeps one loader responsible for
    credentials, and makes a missing key fail here with a clear name instead of
    inside a vendor library.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is empty. The explanation cannot be written "
            "without it, though the cycle itself does not need it. "
            "Set it in .env; it is gitignored."
        )
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        google_api_key=settings.google_api_key,
    )


def response_text(message: Any) -> str:
    """The model's answer as text, whatever shape the provider returned it in.

    Handles a plain string, a list of content blocks, and the mixture of dicts
    and objects LangChain uses across providers. Blocks that are not text —
    reasoning signatures, tool calls, images — are dropped rather than
    stringified, because their repr looks enough like content to survive a
    casual glance at a log.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") in (None, "text") and isinstance(
                block.get("text"), str
            ):
                parts.append(block["text"])
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
