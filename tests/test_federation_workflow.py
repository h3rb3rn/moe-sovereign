"""Federation policy, handshake and manual-review wiring contracts."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from federation.client import LibrisClient
from federation.outbound_policy import filter_bundle_by_policy, get_manual_domains


ROOT = Path(__file__).parents[1]


def _bundle() -> dict:
    return {
        "entities": [
            {"name": "Auto", "domain": "general"},
            {"name": "Manual", "domain": "science"},
            {"name": "Blocked", "domain": "legal_advisor"},
        ],
        "relations": [
            {
                "subject": "Auto",
                "predicate": "is",
                "object": "A",
                "domain": "general",
                "confidence": 0.9,
                "verified": True,
            },
            {
                "subject": "Manual",
                "predicate": "is",
                "object": "M",
                "domain": "science",
                "confidence": 0.8,
                "verified": True,
            },
            {
                "subject": "Blocked",
                "predicate": "is",
                "object": "B",
                "domain": "legal_advisor",
                "confidence": 1.0,
                "verified": True,
            },
        ],
        "syntheses": [],
    }


POLICIES = [
    {"domain": "general", "mode": "auto", "min_confidence": 0.7, "only_verified": True},
    {"domain": "science", "mode": "manual", "min_confidence": 0.7, "only_verified": True},
    {"domain": "legal_advisor", "mode": "blocked", "min_confidence": 0.7, "only_verified": True},
]


def test_auto_and_manual_policies_can_be_split_without_blocked_data():
    auto = filter_bundle_by_policy(_bundle(), [POLICIES[0]])
    manual = filter_bundle_by_policy(_bundle(), [POLICIES[1]])

    assert [item["domain"] for item in auto["entities"]] == ["general"]
    assert [item["domain"] for item in auto["relations"]] == ["general"]
    assert [item["domain"] for item in manual["entities"]] == ["science"]
    assert [item["domain"] for item in manual["relations"]] == ["science"]
    assert get_manual_domains(POLICIES) == ["science"]


@pytest.mark.asyncio
async def test_libris_handshake_calls_protocol_endpoint():
    response = MagicMock(status_code=200)
    response.json.return_value = {"status": "pending"}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)

    with patch("federation.client.httpx.AsyncClient", return_value=client):
        result = await LibrisClient(
            "https://hub.example",
            "",
            "node-1",
        ).handshake("Node One", "https://node.example", ["general"])

    assert result == {"status": "pending"}
    client.post.assert_awaited_once_with(
        "https://hub.example/v1/federation/handshake",
        json={
            "node_id": "node-1",
            "name": "Node One",
            "url": "https://node.example",
            "domains": ["general"],
        },
        headers={"Content-Type": "application/json"},
    )


def test_admin_routes_wire_manual_queue_send_and_auto_scheduler():
    source = (ROOT / "admin_ui" / "app.py").read_text(encoding="utf-8")
    required_markers = [
        '@app.post("/api/federation/register"',
        "result = await client.handshake(",
        "queued.append(await db.create_outbox_entry(",
        '@app.post("/api/federation/outbox/{entry_id}/send"',
        "entry = await db.get_outbox_entry(entry_id)",
        "policy = await db.get_federation_policy(entry[\"domain\"])",
        "await db.update_outbox_status(entry_id, \"sent\")",
        "await api_federation_push(include_manual=False)",
        "asyncio.create_task(_federation_auto_push_loop())",
    ]
    missing = [marker for marker in required_markers if marker not in source]
    assert not missing, "\n".join(missing)
