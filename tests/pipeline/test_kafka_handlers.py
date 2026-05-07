"""
Kafka handler tests — verify the consumer-loop message handlers in isolation.

Uses a _OneShotConsumer fake that yields exactly the messages needed and then
stops, allowing _kafka_consumer_loop to run to completion without a real broker.

Handler mapping (from main.py _kafka_consumer_loop):
  moe.ingest    → graph_manager.extract_and_ingest()
  moe.requests  → logger.debug() only, no side effects
  moe.feedback  → redis_client.zincrby("moe:planner_success", ...) on positive=True
  moe.linting   → graph linting (not tested here)
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared fake: a consumer that yields a fixed set of messages then stops
# ---------------------------------------------------------------------------

class _OneShotConsumer:
    """Fake AIOKafkaConsumer — yields messages list, then raises StopAsyncIteration."""

    def __init__(self, messages):
        self._messages = list(messages)

    async def start(self):
        pass  # succeed immediately — no broker needed

    async def stop(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


def _make_msg(topic: str, value: dict):
    msg = MagicMock()
    msg.topic = topic
    msg.value = value
    return msg


# ---------------------------------------------------------------------------
# _kafka_publish — fire-and-forget helper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kafka_publish_sends_json_payload(monkeypatch):
    """_kafka_publish serializes dict to JSON bytes and sends to broker."""
    mock_producer = AsyncMock()
    # _kafka_publish now lives in services/kafka.py and reads state.kafka_producer
    monkeypatch.setattr("state.kafka_producer", mock_producer)

    from services.kafka import _kafka_publish
    await _kafka_publish("moe.requests", {"response_id": "r-1", "cache_hit": False})

    mock_producer.send_and_wait.assert_called_once()
    topic, raw_bytes = mock_producer.send_and_wait.call_args[0]
    assert topic == "moe.requests"
    payload = json.loads(raw_bytes.decode())
    assert payload["response_id"] == "r-1"


@pytest.mark.asyncio
async def test_kafka_publish_silent_when_producer_is_none(monkeypatch):
    """_kafka_publish must not raise if Kafka is unavailable."""
    monkeypatch.setattr("state.kafka_producer", None)

    from services.kafka import _kafka_publish
    await _kafka_publish("moe.requests", {"key": "value"})  # no exception


# ---------------------------------------------------------------------------
# INGEST handler — calls graph_manager.extract_and_ingest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_handler_calls_extract_and_ingest(monkeypatch):
    """A moe.ingest message triggers graph_manager.extract_and_ingest for non-curator payloads."""
    mock_gm = AsyncMock()
    mock_gm.extract_and_ingest = AsyncMock()
    mock_gm._extract_terms = MagicMock(return_value=[])
    monkeypatch.setattr("main.graph_manager", mock_gm)
    monkeypatch.setattr("main.redis_client", None)   # skip gap detection
    monkeypatch.setattr("main.ingest_llm", MagicMock())
    monkeypatch.setattr("main.judge_llm", MagicMock())

    msg = _make_msg("moe.ingest", {
        "input":        "What is Python?",
        "answer":       "Python is a programming language.",
        "source_model": "llama3",
        "template_name": "research-sovereign",
        "domain":       "programming",
        "confidence":   "0.9",
        "knowledge_type": "factual",
    })
    monkeypatch.setattr("main.AIOKafkaConsumer",
                        MagicMock(return_value=_OneShotConsumer([msg])))

    from main import _kafka_consumer_loop
    await _kafka_consumer_loop()

    mock_gm.extract_and_ingest.assert_called_once()
    kwargs = mock_gm.extract_and_ingest.call_args.kwargs
    assert kwargs["source_model"] == "llama3"
    assert kwargs["knowledge_type"] == "factual"


@pytest.mark.asyncio
async def test_ingest_handler_skips_curator_payloads(monkeypatch):
    """Curator responses must NOT trigger extract_and_ingest (avoids double-ingest loop)."""
    mock_gm = AsyncMock()
    mock_gm.extract_and_ingest = AsyncMock()
    mock_gm._extract_terms = MagicMock(return_value=[])
    monkeypatch.setattr("main.graph_manager", mock_gm)
    monkeypatch.setattr("main.redis_client", None)
    monkeypatch.setattr("main.ingest_llm", MagicMock())
    monkeypatch.setattr("main.judge_llm", MagicMock())

    msg = _make_msg("moe.ingest", {
        "input":         "Is 'mitochondria' in Neo4j?",
        "answer":        "Yes.",
        "source_model":  "ontology-curator-v2",   # curator — must be skipped
        "template_name": "ontology-curator",
    })
    monkeypatch.setattr("main.AIOKafkaConsumer",
                        MagicMock(return_value=_OneShotConsumer([msg])))

    from main import _kafka_consumer_loop
    await _kafka_consumer_loop()

    mock_gm.extract_and_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# REQUESTS handler — logging only, no side effects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_requests_handler_has_no_side_effects(monkeypatch):
    """moe.requests messages are logged but cause no external calls."""
    monkeypatch.setattr("main.graph_manager", None)
    monkeypatch.setattr("main.redis_client", None)

    msg = _make_msg("moe.requests", {
        "response_id": "r-99",
        "cache_hit":   True,
        "expert_models_used": ["llama3"],
    })
    monkeypatch.setattr("main.AIOKafkaConsumer",
                        MagicMock(return_value=_OneShotConsumer([msg])))

    from main import _kafka_consumer_loop
    await _kafka_consumer_loop()   # should complete without raising


# ---------------------------------------------------------------------------
# FEEDBACK handler — TODO(human)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_feedback_positive_saves_planner_pattern(monkeypatch):
    """
    Positive feedback triggers the planner-pattern learning loop.

    The handler reads the plan_cats (expert categories used for the response)
    from Redis, sorts them, joins with '+', and increments the pattern's score
    in the moe:planner_success sorted set with a 180-day TTL.
    """
    mock_redis = AsyncMock()
    # hgetall returns the response metadata that was stored when the query ran
    mock_redis.hgetall = AsyncMock(
        return_value={"plan_cats": '["research","coding"]'}
    )
    monkeypatch.setattr("main.redis_client", mock_redis)
    monkeypatch.setattr("main.graph_manager", None)

    msg = _make_msg("moe.feedback", {
        "response_id": "r-abc",
        "positive":    True,
    })
    monkeypatch.setattr("main.AIOKafkaConsumer",
                        MagicMock(return_value=_OneShotConsumer([msg])))

    from main import _kafka_consumer_loop
    await _kafka_consumer_loop()

    # Signature = sorted categories joined with '+' → "coding+research"
    mock_redis.zincrby.assert_called_once_with("moe:planner_success", 1, "coding+research")

    # TTL must be exactly 180 days so patterns eventually expire
    _DAYS_180 = 60 * 60 * 24 * 180
    mock_redis.expire.assert_called_once_with("moe:planner_success", _DAYS_180)


@pytest.mark.asyncio
async def test_feedback_negative_does_not_save_pattern(monkeypatch):
    """Negative feedback (positive=False) must NOT write to moe:planner_success."""
    mock_redis = AsyncMock()
    monkeypatch.setattr("main.redis_client", mock_redis)
    monkeypatch.setattr("main.graph_manager", None)

    msg = _make_msg("moe.feedback", {
        "response_id": "r-bad",
        "positive":    False,
    })
    monkeypatch.setattr("main.AIOKafkaConsumer",
                        MagicMock(return_value=_OneShotConsumer([msg])))

    from main import _kafka_consumer_loop
    await _kafka_consumer_loop()

    mock_redis.zincrby.assert_not_called()
    mock_redis.expire.assert_not_called()
