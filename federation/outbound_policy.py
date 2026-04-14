"""Outbound policy engine — decides what knowledge to share and how."""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Valid domain names (must match EXPERT_CATEGORIES in app.py)
VALID_DOMAINS = {
    "general", "code_reviewer", "technical_support", "creative_writer",
    "math", "science", "legal_advisor", "medical_consult", "reasoning",
    "data_analyst", "translation", "vision",
}

# Valid policy modes
MODES = {"auto", "manual", "blocked"}


def filter_bundle_by_policy(bundle: dict, policies: list[dict]) -> dict:
    """Filter a knowledge bundle according to domain policies.

    Args:
        bundle: JSON-LD bundle from export_knowledge_bundle()
        policies: List of policy dicts from federation_domain_policy table

    Returns:
        Filtered bundle with only entities/relations matching policy criteria.
        Includes a '_policy_summary' key with filtering stats.
    """
    policy_map = {p["domain"]: p for p in policies}

    # Determine which domains are allowed (auto or manual mode)
    allowed_domains = {
        d for d, p in policy_map.items()
        if p["mode"] in ("auto", "manual")
    }

    if not allowed_domains:
        return {
            **bundle,
            "entities": [],
            "relations": [],
            "syntheses": [],
            "_policy_summary": {"allowed_domains": [], "filtered_reason": "no domains enabled"},
        }

    filtered_entities = []
    filtered_relations = []
    filtered_syntheses = []

    stats = {
        "entities_total": len(bundle.get("entities", [])),
        "relations_total": len(bundle.get("relations", [])),
        "entities_passed": 0,
        "relations_passed": 0,
        "entities_blocked": 0,
        "relations_blocked_domain": 0,
        "relations_blocked_confidence": 0,
        "relations_blocked_unverified": 0,
    }

    # Filter entities by domain
    for entity in bundle.get("entities", []):
        domain = entity.get("domain", "general")
        if domain not in allowed_domains:
            stats["entities_blocked"] += 1
            continue
        filtered_entities.append(entity)
        stats["entities_passed"] += 1

    # Filter relations by domain + confidence + verified
    allowed_entity_names = {e.get("name") for e in filtered_entities}
    for rel in bundle.get("relations", []):
        domain = rel.get("domain", "general")

        if domain not in allowed_domains:
            stats["relations_blocked_domain"] += 1
            continue

        policy = policy_map.get(domain, {})
        min_conf = policy.get("min_confidence", 0.7)
        only_verified = policy.get("only_verified", True)

        confidence = rel.get("confidence", 0) or rel.get("trust_score", 0)
        if confidence < min_conf:
            stats["relations_blocked_confidence"] += 1
            continue

        if only_verified and not rel.get("verified", False):
            stats["relations_blocked_unverified"] += 1
            continue

        filtered_relations.append(rel)
        stats["relations_passed"] += 1

    # Filter syntheses by domain
    for synth in bundle.get("syntheses", []):
        domain = synth.get("domain", "general")
        if domain in allowed_domains:
            filtered_syntheses.append(synth)

    result = {
        **bundle,
        "entities": filtered_entities,
        "relations": filtered_relations,
        "syntheses": filtered_syntheses,
        "_policy_summary": {
            "allowed_domains": sorted(allowed_domains),
            **stats,
        },
    }

    logger.info(
        "Federation outbound filter: %d/%d entities, %d/%d relations passed "
        "(blocked: %d domain, %d confidence, %d unverified)",
        stats["entities_passed"], stats["entities_total"],
        stats["relations_passed"], stats["relations_total"],
        stats["relations_blocked_domain"],
        stats["relations_blocked_confidence"],
        stats["relations_blocked_unverified"],
    )

    return result


def get_auto_push_domains(policies: list[dict]) -> list[str]:
    """Return domains configured for automatic push."""
    return [p["domain"] for p in policies if p["mode"] == "auto"]


def get_manual_domains(policies: list[dict]) -> list[str]:
    """Return domains configured for manual review before push."""
    return [p["domain"] for p in policies if p["mode"] == "manual"]
