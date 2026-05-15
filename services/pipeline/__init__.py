"""services/pipeline/__init__.py — re-export shim for the pipeline package.

This package was extracted from a single ``services/pipeline.py`` file. The
re-exports below preserve the original public API so callers can keep using
``from services.pipeline import X``.
"""

from services.pipeline.chat import (
    Message,
    ChatCompletionRequest,
    chat_completions,
)
from services.pipeline.cc_session import CCSession, _resolve_cc_session
from services.pipeline.anthropic import (
    _anthropic_tool_handler,
    _anthropic_reasoning_handler,
    _anthropic_moe_handler,
    anthropic_messages,
)
from services.pipeline.ollama import (
    _ollama_internal_stream,
)
from services.pipeline.responses import (
    _ResponsesRequest,
    responses_api,
    _invoke_pipeline_for_responses,
    _stream_responses_api,
)

__all__ = [
    "CCSession",
    "_resolve_cc_session",
    "Message",
    "ChatCompletionRequest",
    "chat_completions",
    "_anthropic_tool_handler",
    "_anthropic_reasoning_handler",
    "_anthropic_moe_handler",
    "anthropic_messages",
    "_ollama_internal_stream",
    "_ResponsesRequest",
    "responses_api",
    "_invoke_pipeline_for_responses",
    "_stream_responses_api",
]
