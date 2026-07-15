"""
tests/test_tracking.py — services.tracking._record_stage unit tests.

Covers the fire-and-forget pipeline-stage trace helper used by the
live-pipeline-visualization diagram (Redis list moe:active:{chat_id}:trace,
bounded to 30 entries, TTL refreshed on every write).
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import state
from services.tracking import (
    _record_stage, _record_file_touch,
    _touch_model_recently_used, _is_model_busy_elsewhere, _recent_use_key,
    _RECENT_USE_GRACE_SECONDS,
    _pstop_key, _record_premature_stop_outcome, _get_premature_stop_rate,
    _PSTOP_MIN_SAMPLES,
)


def _mock_redis_with_pipeline():
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock()
    mock_redis = MagicMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)
    return mock_redis, mock_pipe


@pytest.mark.asyncio
async def test_record_stage_writes_rpush_ltrim_expire():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "cache", "hit_l0", "detail-text")

    mock_redis.pipeline.assert_called_once()
    assert mock_pipe.rpush.call_count == 1
    key, entry_json = mock_pipe.rpush.call_args[0]
    assert key == "moe:active:chat123:trace"
    entry = json.loads(entry_json)
    assert entry["stage"] == "cache"
    assert entry["status"] == "hit_l0"
    assert entry["detail"] == "detail-text"
    assert isinstance(entry["ts"], float)

    # Bounded to the 30 most recent entries
    mock_pipe.ltrim.assert_called_once_with("moe:active:chat123:trace", -30, -1)
    # TTL refreshed on every write, matching moe:active:{chat_id}'s 7200s TTL
    mock_pipe.expire.assert_called_once_with("moe:active:chat123:trace", 7200)
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_stage_default_status_started():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "planner")

    entry = json.loads(mock_pipe.rpush.call_args[0][1])
    assert entry["status"] == "started"
    assert entry["detail"] == ""


@pytest.mark.asyncio
async def test_record_stage_noop_without_chat_id():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("", "cache", "started")

    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_stage_noop_without_redis_client():
    with patch.object(state, "redis_client", None):
        # Must not raise even though there is no Redis connection.
        await _record_stage("chat123", "cache", "started")


@pytest.mark.asyncio
async def test_record_stage_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        # Fire-and-forget: errors are swallowed (logged, not raised).
        await _record_stage("chat123", "cache", "started")


# ── _record_file_touch — live-pipeline-visualization "which files did this
# Agent Tool Path session touch" view (Redis list moe:active:{chat_id}:files,
# bounded to 200 entries — a long coding session easily touches more files
# than pipeline stages) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_file_touch_writes_rpush_ltrim_expire():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "/repo/main.py", "write", "Edit")

    key, entry_json = mock_pipe.rpush.call_args[0]
    assert key == "moe:active:chat123:files"
    entry = json.loads(entry_json)
    assert entry["path"] == "/repo/main.py"
    assert entry["action"] == "write"
    assert entry["tool"] == "Edit"
    assert isinstance(entry["ts"], float)

    mock_pipe.ltrim.assert_called_once_with("moe:active:chat123:files", -200, -1)
    mock_pipe.expire.assert_called_once_with("moe:active:chat123:files", 7200)
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_file_touch_noop_without_path():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "", "read", "Read")

    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_file_touch_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        await _record_file_touch("chat123", "/x.py", "read", "Read")


# ── _touch_model_recently_used / _is_model_busy_elsewhere grace period ─────
# Covers the gap between an agentic client's individual stateless HTTP turns
# (no moe:active:* entry exists while the user reads/types the next message)
# — confirmed live: an unrelated interactive-pipeline request unloaded a
# model out from under an otherwise still-ongoing OpenCode session because
# the in-flight-only check saw nothing "in use" during that gap.

@pytest.mark.asyncio
async def test_touch_model_recently_used_sets_key_with_grace_ttl():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()
    with patch.object(state, "redis_client", mock_redis):
        await _touch_model_recently_used("qwen3.6:35b", "http://192.168.155.224:11434")

    mock_redis.set.assert_awaited_once()
    args, kwargs = mock_redis.set.call_args
    assert args[0] == _recent_use_key("qwen3.6:35b", "http://192.168.155.224:11434")
    assert kwargs.get("ex") == _RECENT_USE_GRACE_SECONDS


@pytest.mark.asyncio
async def test_touch_model_recently_used_noop_without_model_or_url():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()
    with patch.object(state, "redis_client", mock_redis):
        await _touch_model_recently_used("", "http://x:11434")
        await _touch_model_recently_used("qwen3.6:35b", "")
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_touch_model_recently_used_never_raises_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch.object(state, "redis_client", mock_redis):
        await _touch_model_recently_used("qwen3.6:35b", "http://x:11434")


@pytest.mark.asyncio
async def test_is_model_busy_elsewhere_true_when_recently_used_key_present():
    mock_redis = MagicMock()
    mock_redis.exists = AsyncMock(return_value=1)
    with patch.object(state, "redis_client", mock_redis):
        busy = await _is_model_busy_elsewhere("qwen3.6:35b", "http://192.168.155.224:11434")
    assert busy is True
    # Recently-used short-circuits before ever scanning moe:active:* — cheap
    # and covers the exact gap this feature exists for.
    mock_redis.scan_iter.assert_not_called()


@pytest.mark.asyncio
async def test_is_model_busy_elsewhere_false_when_neither_recent_nor_active():
    mock_redis = MagicMock()
    mock_redis.exists = AsyncMock(return_value=0)

    async def _empty_scan(_pattern):
        return
        yield  # pragma: no cover - makes this an async generator

    mock_redis.scan_iter = _empty_scan
    with patch.object(state, "redis_client", mock_redis):
        busy = await _is_model_busy_elsewhere("qwen3.6:35b", "http://192.168.155.224:11434")
    assert busy is False


# ── _record_premature_stop_outcome / _get_premature_stop_rate — persists the
# looks_like_premature_stop() signal per (model, node) pair so a future
# _select_node() weighting can avoid a node/model combo that keeps stalling.
# Same Redis-hash counter shape as services/inference.py's
# moe:perf:{model}:{category} Thompson-sampling counters.

def test_pstop_key_sanitizes_model_and_node():
    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    assert key == "moe:pstop:qwen3_6_35b:http:__192.168.155.224:11434"


def test_pstop_key_same_node_different_url_forms_collapse():
    # _norm_base_url should make "http://host:port" and "http://host:port/v1"
    # collide on the same key, same as _latency_key already relies on.
    key_a = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    key_b = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434/v1")
    assert key_a == key_b


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_increments_total_and_premature():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://192.168.155.224:11434", True)

    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    mock_pipe.hincrby.assert_any_call(key, "total", 1)
    mock_pipe.hincrby.assert_any_call(key, "premature", 1)
    mock_pipe.expire.assert_called_once()
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_clean_turn_only_increments_total():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://192.168.155.224:11434", False)

    key = _pstop_key("qwen3.6:35b", "http://192.168.155.224:11434")
    mock_pipe.hincrby.assert_called_once_with(key, "total", 1)


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_noop_without_model_or_node():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("", "http://x:11434", True)
        await _record_premature_stop_outcome("qwen3.6:35b", "", True)
    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_premature_stop_outcome_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        await _record_premature_stop_outcome("qwen3.6:35b", "http://x:11434", True)


@pytest.mark.asyncio
async def test_get_premature_stop_rate_optimistic_below_min_samples():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={"total": str(_PSTOP_MIN_SAMPLES - 1), "premature": "1"})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0


@pytest.mark.asyncio
async def test_get_premature_stop_rate_computes_fraction_once_enough_samples():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={"total": "10", "premature": "3"})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.3


@pytest.mark.asyncio
async def test_get_premature_stop_rate_no_data_returns_zero():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0


@pytest.mark.asyncio
async def test_get_premature_stop_rate_never_raises_on_redis_error():
    mock_redis = MagicMock()
    mock_redis.hgetall = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch.object(state, "redis_client", mock_redis):
        rate = await _get_premature_stop_rate("qwen3.6:35b", "http://x:11434")
    assert rate == 0.0
