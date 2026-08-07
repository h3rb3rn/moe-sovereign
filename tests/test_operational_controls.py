"""Disabled maintenance jobs and runtime toggles must be enforced, not decorative."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.rlsf_local_loop import run_rlsf_loop
from starfleet_config import set_feature_enabled

ROOT = Path(__file__).parents[1]

@pytest.mark.asyncio
async def test_disabled_rlsf_loop_does_no_work():
    with (
        patch("services.rlsf_local_loop.is_enabled", return_value=False),
        patch(
            "services.rlsf_local_loop.find_unevaluated_chats",
            new=AsyncMock(),
        ) as find,
    ):
        result = await run_rlsf_loop()

    assert result["details"]["status"] == "disabled"
    find.assert_not_awaited()


def test_rlsf_http_trigger_enforces_same_enable_switch():
    source = (ROOT / "routes" / "admin_rlsf.py").read_text(encoding="utf-8")
    assert "if not is_enabled():" in source
    assert "status_code=409" in source
    assert source.index("if not is_enabled():") < source.index("background_tasks.add_task")


@pytest.mark.asyncio
async def test_feature_setter_writes_validated_redis_override():
    redis_client = AsyncMock()
    assert await set_feature_enabled("watchdog", False, redis_client)
    redis_client.set.assert_awaited_once_with("moe:features:watchdog", "false")


@pytest.mark.asyncio
async def test_feature_setter_rejects_unknown_feature():
    redis_client = AsyncMock()
    assert not await set_feature_enabled("unknown", True, redis_client)
    redis_client.set.assert_not_awaited()


def test_feature_write_route_is_authenticated_and_uses_setter():
    source = (ROOT / "routes" / "watchdog.py").read_text(encoding="utf-8")
    assert '@router.put("/api/starfleet/features/{name}")' in source
    assert "await _require_feature_admin(request)" in source
    assert "_starfleet.set_feature_enabled(" in source
