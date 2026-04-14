"""Federation sync logic — push and pull knowledge to/from MoE Libris hubs."""

import json
import logging
from typing import Optional, Callable, Awaitable

from federation.client import LibrisClient, LibrisError
from federation.outbound_policy import filter_bundle_by_policy, get_auto_push_domains

logger = logging.getLogger(__name__)


async def push_knowledge(
    client: LibrisClient,
    graph_rag_manager,
    policies: list[dict],
    kafka_publish_fn: Optional[Callable] = None,
) -> dict:
    """Export knowledge according to policies and push to the Libris hub.

    For 'auto' domains: push immediately.
    For 'manual' domains: return the filtered bundle for outbox queuing.

    Returns:
        {"auto_result": {...}, "manual_bundle": {...} | None, "stats": {...}}
    """
    auto_domains = get_auto_push_domains(policies)
    if not auto_domains:
        return {"auto_result": None, "manual_bundle": None,
                "stats": {"skipped": "no auto-push domains configured"}}

    # Export from local graph
    bundle = await graph_rag_manager.export_knowledge_bundle(
        domains=auto_domains,
        min_trust=0.3,
        include_syntheses=True,
        strip_sensitive=True,
    )

    # Apply policy filters (confidence, verified-only)
    filtered = filter_bundle_by_policy(bundle, policies)
    summary = filtered.pop("_policy_summary", {})

    entity_count = len(filtered.get("entities", []))
    triple_count = len(filtered.get("relations", []))

    if entity_count == 0 and triple_count == 0:
        logger.info("Federation push: nothing to send after policy filtering")
        return {"auto_result": None, "manual_bundle": None,
                "stats": {"skipped": "no data passed policy filters", **summary}}

    # Push to hub
    try:
        result = await client.push(filtered)
        logger.info(
            "Federation push: %d entities, %d triples → hub responded: %s",
            entity_count, triple_count, result.get("detail", "ok"),
        )

        if kafka_publish_fn:
            await kafka_publish_fn("moe.audit", {
                "type": "federation_push",
                "hub_url": client.hub_url,
                "entities": entity_count,
                "triples": triple_count,
                "result": result,
            })

        return {"auto_result": result, "manual_bundle": None,
                "stats": {**summary, "entities_sent": entity_count,
                          "triples_sent": triple_count}}
    except LibrisError as e:
        logger.error("Federation push failed: %s", e)
        return {"auto_result": None, "manual_bundle": None,
                "stats": {"error": str(e), **summary}}


async def pull_knowledge(
    client: LibrisClient,
    graph_rag_manager,
    last_sync: Optional[str] = None,
    domains: Optional[list[str]] = None,
    trust_floor: float = 0.5,
    kafka_publish_fn: Optional[Callable] = None,
) -> dict:
    """Pull approved knowledge from the Libris hub and import locally.

    Returns:
        Import statistics from graph_rag_manager.import_knowledge_bundle()
    """
    try:
        response = await client.pull(last_sync=last_sync, domains=domains)
    except LibrisError as e:
        logger.error("Federation pull failed: %s", e)
        return {"error": str(e)}

    bundle = response.get("bundle", response)
    total = response.get("total", 0)

    if total == 0:
        logger.info("Federation pull: no new data since %s", last_sync or "beginning")
        return {"entities_created": 0, "relations_created": 0,
                "detail": "no new data"}

    # Import into local graph
    result = await graph_rag_manager.import_knowledge_bundle(
        bundle=bundle,
        source_tag="libris",
        trust_floor=trust_floor,
        dry_run=False,
        kafka_publish_fn=kafka_publish_fn,
    )

    logger.info(
        "Federation pull: imported %d entities, %d relations "
        "(skipped: %d entities, %d relations, contradictions: %d)",
        result.get("entities_created", 0),
        result.get("relations_created", 0),
        result.get("entities_skipped", 0),
        result.get("relations_skipped", 0),
        len(result.get("contradictions", [])),
    )

    if kafka_publish_fn:
        await kafka_publish_fn("moe.audit", {
            "type": "federation_pull",
            "hub_url": client.hub_url,
            "result": {k: v for k, v in result.items() if k != "contradictions"},
        })

    return result
