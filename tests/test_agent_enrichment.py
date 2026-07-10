"""tests/test_agent_enrichment.py — Unit tests for services/agent_enrichment.py.

classify_turn, is_informational, extract_workspace and make_scope are pure,
deterministic functions operating on plain dicts/strings only — no I/O.
agent_graph_context, agent_writeback and agent_cache_lookup are exercised
with AsyncMock/MagicMock stand-ins for redis_client, graph_manager and the
ChromaDB collection (no live Redis/Neo4j/Chroma). Kafka publish and the async
judge call are patched at their import sites in services.agent_enrichment.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent_enrichment import (
    TurnInfo,
    classify_turn,
    is_informational,
    extract_workspace,
    make_scope,
    agent_cache_lookup,
    agent_graph_context,
    agent_writeback,
)


# ── classify_turn — Anthropic content-block format ───────────────────────────

class TestClassifyTurnAnthropic:

    def test_initial_task_with_tools_no_tool_result(self):
        messages = [{"role": "user", "content": "Fix the bug in main.py"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "initial_task"

    def test_mid_loop_with_tool_result_block(self):
        messages = [
            {"role": "user", "content": "Fix the bug in main.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"

    def test_tool_result_in_middle_message_still_detected(self):
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            {"role": "assistant", "content": "done for now"},
            {"role": "user", "content": "what's next?"},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"

    def test_pure_text_chat_no_tools(self):
        messages = [{"role": "user", "content": "What is a monad?"}]
        info = classify_turn(messages, tools=None, api="anthropic")
        assert info.kind == "initial_task"

    def test_empty_tools_list(self):
        messages = [{"role": "user", "content": "hi"}]
        info = classify_turn(messages, tools=[], api="anthropic")
        assert info.kind == "initial_task"


# ── classify_turn — OpenAI role format ────────────────────────────────────────

class TestClassifyTurnOpenAI:

    def test_initial_task_no_tool_role(self):
        messages = [{"role": "user", "content": "Explain this repo"}]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "initial_task"

    def test_mid_loop_with_role_tool(self):
        messages = [
            {"role": "user", "content": "Refactor the parser"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "done"},
        ]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "mid_loop"

    def test_object_style_messages_with_role_attribute(self):
        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        messages = [Msg("user", "hello"), Msg("tool", "result")]
        info = classify_turn(messages, tools=[{"type": "function"}], api="openai")
        assert info.kind == "mid_loop"


# ── classify_turn — cacheable flag ────────────────────────────────────────────

class TestClassifyTurnCacheable:

    def test_initial_informational_question_is_cacheable(self):
        messages = [{"role": "user", "content": "What is dependency injection?"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.cacheable is True

    def test_initial_mutation_task_is_not_cacheable(self):
        messages = [{"role": "user", "content": "Fix the failing test in auth.py"}]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.cacheable is False

    def test_mid_loop_never_cacheable_even_if_informational(self):
        messages = [
            {"role": "user", "content": "What does this function do?"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        info = classify_turn(messages, tools=[{"name": "bash"}], api="anthropic")
        assert info.kind == "mid_loop"
        assert info.cacheable is False


# ── is_informational ──────────────────────────────────────────────────────────

class TestIsInformational:

    @pytest.mark.parametrize("query", [
        "What is a monad?",
        "Why does this test fail?",
        "How does the LangGraph state machine work?",
        "Explain the caching layer.",
        "Was macht diese Funktion?",
        "Warum schlägt der Test fehl?",
        "Wie funktioniert der Router?",
        "Erkläre die Architektur dieses Projekts",
    ])
    def test_positive_cases(self, query):
        assert is_informational(query) is True

    @pytest.mark.parametrize("query", [
        "Fix the bug in main.py",
        "Implement a new endpoint for /v1/foo",
        "Refactor the parser module",
        "Ändere die Konfigurationsdatei",
        "Schreibe einen neuen Test",
        "How do I fix this failing test?",   # question form but mutation verb wins
        "Wie behebe ich diesen Fehler?",
        "",
        "   ",
    ])
    def test_negative_cases(self, query):
        assert is_informational(query) is False


# ── extract_workspace / make_scope ────────────────────────────────────────────

class TestExtractWorkspace:

    def test_from_system_text(self):
        system = "You are Claude Code.\nWorking directory: /opt/project/foo\nOther stuff."
        assert extract_workspace(system, []) == "/opt/project/foo"

    def test_falls_back_to_first_user_message(self):
        messages = [{"role": "user", "content": "Working directory: /home/dev/bar\nHelp me."}]
        assert extract_workspace("", messages) == "/home/dev/bar"

    def test_falls_back_to_global(self):
        assert extract_workspace("", [{"role": "user", "content": "hi"}]) == "global"

    def test_no_system_no_messages(self):
        assert extract_workspace("", None) == "global"


class TestMakeScope:

    def test_same_user_same_workspace_same_scope(self):
        assert make_scope("u1", "/proj/a") == make_scope("u1", "/proj/a")

    def test_different_workspace_different_scope(self):
        assert make_scope("u1", "/proj/a") != make_scope("u1", "/proj/b")

    def test_different_user_different_scope(self):
        assert make_scope("u1", "/proj/a") != make_scope("u2", "/proj/a")

    def test_scope_is_16_hex_chars(self):
        scope = make_scope("u1", "/proj/a")
        assert len(scope) == 16
        int(scope, 16)  # raises if not valid hex


# ── agent_cache_lookup ─────────────────────────────────────────────────────────

class TestAgentCacheLookup:

    @pytest.mark.asyncio
    async def test_no_collection_is_a_safe_miss(self):
        result = await agent_cache_lookup("q", "scope", None, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_query_is_a_safe_miss(self):
        collection = MagicMock()
        result = await agent_cache_lookup("", "scope", None, collection)
        assert result is None
        collection.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_l0_exact_hit_skips_l1_query(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "x" * 100
        collection = MagicMock()
        result = await agent_cache_lookup("what is X?", "scope1", redis_client, collection)
        assert result == "x" * 100
        collection.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_l0_short_value_is_ignored_falls_through_to_l1(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "short"  # <= 50 chars, per interactive-cache convention
        collection = MagicMock()
        collection.query.return_value = {"documents": [[]], "distances": [[]], "metadatas": [[]]}
        result = await agent_cache_lookup("what is X?", "scope1", redis_client, collection)
        assert result is None
        collection.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_l1_hit_high_confidence_fresh_not_flagged(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.9, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result == "cached answer text"
        call_kwargs = collection.query.call_args.kwargs
        assert call_kwargs["where"] == {"scope": "scope1"}

    @pytest.mark.asyncio
    async def test_l1_miss_low_confidence(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.5, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_miss_stale_entry(self):
        collection = MagicMock()
        stale_ts = (datetime.now() - timedelta(days=30)).isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.10]],
            "metadatas": [[{"confidence": 0.9, "ts": stale_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_miss_distance_too_far(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["cached answer text"]],
            "distances": [[0.40]],  # above KNOWLEDGE_BYPASS_THRESHOLD (0.25)
            "metadatas": [[{"confidence": 0.9, "ts": fresh_ts, "flagged": False}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_l1_flagged_entry_never_served(self):
        collection = MagicMock()
        fresh_ts = datetime.now().isoformat()
        collection.query.return_value = {
            "documents": [["bad cached answer"]],
            "distances": [[0.05]],
            "metadatas": [[{"confidence": 0.95, "ts": fresh_ts, "flagged": True}]],
        }
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_documents_is_a_miss(self):
        collection = MagicMock()
        collection.query.return_value = {"documents": [[]], "distances": [[]], "metadatas": [[]]}
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_is_a_safe_miss(self):
        collection = MagicMock()

        def _slow_query(*a, **kw):
            import time as _time
            _time.sleep(1)
            return {"documents": [[]], "distances": [[]], "metadatas": [[]]}

        collection.query.side_effect = _slow_query
        with patch("services.agent_enrichment.AGENT_CACHE_MAX_LOOKUP_MS", 10):
            result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_exception_is_a_safe_miss(self):
        collection = MagicMock()
        collection.query.side_effect = Exception("chroma down")
        result = await agent_cache_lookup("what is X?", "scope1", None, collection)
        assert result is None


# ── agent_writeback ────────────────────────────────────────────────────────────

class TestAgentWriteback:

    @pytest.mark.asyncio
    async def test_short_answer_skipped_no_kafka_no_upsert(self):
        collection = MagicMock()
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", "a", "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        collection.upsert.assert_not_called()
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_collection_skipped(self):
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", "x" * 200, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, None,
            )
        assert result is None
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_clean_answer_upserts_at_initial_confidence_and_publishes_kafka(self):
        collection = MagicMock()
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka, \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", False):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        collection.upsert.assert_called_once()
        upsert_kwargs = collection.upsert.call_args.kwargs
        assert upsert_kwargs["documents"] == [answer]
        assert upsert_kwargs["metadatas"][0]["confidence"] == 0.6
        assert upsert_kwargs["metadatas"][0]["flagged"] is False
        assert upsert_kwargs["metadatas"][0]["scope"] == "scope1"

        mock_kafka.assert_called_once()
        topic, payload = mock_kafka.call_args.args
        assert payload["input"] == "what is X?"
        assert payload["answer"] == answer
        assert payload["source_model"] == "model-a"
        assert payload["tenant_id"] == "user:u1"
        assert payload["confidence"] == 0.6

    @pytest.mark.asyncio
    async def test_kafka_failure_does_not_raise_and_still_reports_written(self):
        collection = MagicMock()
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock(side_effect=Exception("boom"))), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", False):
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        collection.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_raise_and_skips_kafka(self):
        collection = MagicMock()
        collection.upsert.side_effect = Exception("chroma down")
        answer = "x" * 200
        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()) as mock_kafka:
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
        assert result is None
        mock_kafka.assert_not_called()

    @pytest.mark.asyncio
    async def test_judge_promotion_updates_confidence_and_writes_l0(self):
        collection = MagicMock()
        redis_client = AsyncMock()
        answer = "x" * 200

        judge_result = MagicMock()
        judge_result.content = "SELF_RATING: 5"

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(return_value=judge_result)):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                redis_client, collection,
            )
            # The judge promotion runs as a fire-and-forget asyncio.create_task that
            # itself awaits asyncio.to_thread() — give the thread pool a moment.
            await asyncio.sleep(0.05)

        assert collection.upsert.call_count == 2  # initial write + promotion
        promoted_metadata = collection.upsert.call_args.kwargs["metadatas"][0]
        assert promoted_metadata["confidence"] == 0.9
        redis_client.setex.assert_called_once()
        l0_key = redis_client.setex.call_args.args[0]
        assert l0_key.startswith("moe:agent:qcache:scope1:")

    @pytest.mark.asyncio
    async def test_judge_low_score_flags_entry(self):
        collection = MagicMock()
        answer = "x" * 200

        judge_result = MagicMock()
        judge_result.content = "SELF_RATING: 1"

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(return_value=judge_result)):
            await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
            await asyncio.sleep(0.05)

        assert collection.upsert.call_count == 2
        flagged_metadata = collection.upsert.call_args.kwargs["metadatas"][0]
        assert flagged_metadata["flagged"] is True

    @pytest.mark.asyncio
    async def test_judge_failure_does_not_raise(self):
        collection = MagicMock()
        answer = "x" * 200

        with patch("services.agent_enrichment._kafka_publish", new=AsyncMock()), \
             patch("services.agent_enrichment.AGENT_INGEST_JUDGE", True), \
             patch("services.inference.ainvoke_judge_llm", new=AsyncMock(side_effect=Exception("llm down"))):
            result = await agent_writeback(
                "what is X?", answer, "scope1", "user:u1", "u1", "model-a", "sess1",
                None, collection,
            )
            await asyncio.sleep(0.05)

        assert result is None
        # Only the initial write — judge failed before any promotion/flag upsert.
        assert collection.upsert.call_count == 1


# ── agent_graph_context ────────────────────────────────────────────────────────

class TestAgentGraphContext:

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty_without_touching_graph(self):
        graph_manager = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="should not be called")
        result = await agent_graph_context(
            "", ["user:u1"], "sess", None, graph_manager, max_chars=1000, timeout_s=1.0
        )
        assert result == ""
        graph_manager.query_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_graph_manager_returns_empty(self):
        result = await agent_graph_context(
            "what is this?", ["user:u1"], "sess", None, None, max_chars=1000, timeout_s=1.0
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_l2_cache_hit_skips_graph_query(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = "cached graph context"
        graph_manager = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="should not be called")
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert result == "cached graph context"
        graph_manager.query_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_graph_and_writes_back(self, monkeypatch):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="fresh graph facts")
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert result == "fresh graph facts"
        graph_manager.query_context.assert_called_once()
        call_kwargs = graph_manager.query_context.call_args
        assert call_kwargs.kwargs["tenant_ids"] == ["user:u1"]

    @pytest.mark.asyncio
    async def test_result_capped_to_max_chars(self, monkeypatch):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="x" * 5000)
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=100, timeout_s=1.0,
        )
        assert len(result) == 100

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_string(self):
        redis_client = AsyncMock()
        redis_client.get.return_value = None
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()

        async def _slow(*a, **kw):
            import asyncio as _asyncio
            await _asyncio.sleep(10)
            return "too slow"

        graph_manager.query_context = _slow
        result = await agent_graph_context(
            "what is X?", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=0.01,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_different_tenants_get_different_cache_keys(self, monkeypatch):
        redis_client = AsyncMock()
        seen_keys = []

        async def _get(key):
            seen_keys.append(key)
            return None

        redis_client.get.side_effect = _get
        graph_manager = MagicMock()
        graph_manager.driver = MagicMock()
        graph_manager.query_context = AsyncMock(return_value="")
        import episodic_memory
        monkeypatch.setattr(episodic_memory, "get_episode_hint", AsyncMock(return_value=""))
        await agent_graph_context(
            "same query", ["user:u1"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        await agent_graph_context(
            "same query", ["user:u2"], "sess", redis_client, graph_manager,
            max_chars=1000, timeout_s=1.0,
        )
        assert len(seen_keys) == 2
        assert seen_keys[0] != seen_keys[1]
