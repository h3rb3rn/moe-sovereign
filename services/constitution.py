"""
services/constitution.py — Sovereign Constitution enforcement engine.

Loads configs/sovereign-constitution.yaml and evaluates each enabled rule
against the final response and request state before the response reaches
the client. All checks are deterministic — no LLM calls.

Design:
  - Fail-open: YAML load failures or check exceptions never block a response.
  - Auditable: every violation is emitted to Kafka (moe.audit) asynchronously.
  - Extensible: add a new check handler to _CHECK_HANDLERS and a rule to the
    YAML — no other changes needed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import yaml

logger = logging.getLogger("MOE-SOVEREIGN")

_CONSTITUTION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "sovereign-constitution.yaml",
)

_CONSTITUTION: Optional[dict] = None


def _load_constitution() -> dict:
    global _CONSTITUTION
    if _CONSTITUTION is not None:
        return _CONSTITUTION
    try:
        with open(_CONSTITUTION_PATH, "r", encoding="utf-8") as f:
            _CONSTITUTION = yaml.safe_load(f)
        logger.info(
            "⚖️  Constitution loaded from %s: %d rules",
            _CONSTITUTION_PATH,
            len(_CONSTITUTION.get("constitution", [])),
        )
    except FileNotFoundError:
        logger.warning("⚖️  sovereign-constitution.yaml not found — enforcement skipped")
        _CONSTITUTION = {"constitution": []}
    except Exception as e:
        logger.error("⚖️  Failed to load constitution: %s", e)
        _CONSTITUTION = {"constitution": []}
    return _CONSTITUTION


# ── Credential leak pattern ───────────────────────────────────────────────────
_CREDENTIAL_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9]{20,}"              # OpenAI / Anthropic API keys
    r"|glpat-[A-Za-z0-9_\-]{10,}"      # GitLab personal access tokens
    r"|ghp_[A-Za-z0-9]{30,}"           # GitHub personal access tokens
    r"|AKIA[A-Z0-9]{16}"               # AWS access key IDs
    r"|(?:password|passwd)\s*[:=]\s*\S{8,}"  # generic password assignments
    r"|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"  # PEM key blocks
    r")",
    re.I,
)

# ── Disclaimer pattern for safety-critical domains ────────────────────────────
_DISCLAIMER_RE = re.compile(
    r"\b("
    r"consult|professional|doctor|lawyer|attorney|physician|"
    r"Arzt|Anwalt|Rechtsanwalt|Facharzt|"
    r"medizinischen Rat|rechtlichen Rat|"
    r"this is not (?:medical|legal) advice|"
    r"keine (?:medizinische|rechtliche) Beratung"
    r")\b",
    re.I,
)

_SAFETY_CRITICAL_CATS = {"medical_consult", "legal_advisor"}


# ── Check handlers ────────────────────────────────────────────────────────────

def _check_endpoints_not_external(response_text: str, state_: dict) -> Optional[str]:
    egress = state_.get("federation_egress")
    if egress:
        return f"Outbound federation egress detected: {egress}"
    return None


def _check_llm_endpoints_local(response_text: str, state_: dict) -> Optional[str]:
    external_calls = state_.get("external_llm_calls") or []
    if external_calls and not (state_.get("user_permissions") or {}).get("allow_external_llm"):
        return f"External LLM calls without user permission: {external_calls}"
    return None


def _check_response_no_credentials(response_text: str, state_: dict) -> Optional[str]:
    m = _CREDENTIAL_RE.search(response_text)
    if m:
        masked = m.group(0)[:6] + "***"
        return f"Potential credential pattern in response: {masked}"
    return None


def _check_kafka_audit_emitted(response_text: str, state_: dict) -> Optional[str]:
    if not state_.get("response_id"):
        return "No response_id set — audit trail cannot be linked to this request"
    return None


def _check_safety_critical_has_disclaimer(response_text: str, state_: dict) -> Optional[str]:
    plan = state_.get("plan") or []
    plan_cats = {t.get("category", "") for t in plan if isinstance(t, dict)}
    active = plan_cats & _SAFETY_CRITICAL_CATS
    if active and not _DISCLAIMER_RE.search(response_text):
        return (
            f"Safety-critical domain [{', '.join(sorted(active))}] response "
            f"is missing a professional-advice disclaimer"
        )
    return None


_CHECK_HANDLERS = {
    "endpoints_not_external":         _check_endpoints_not_external,
    "llm_endpoints_local":            _check_llm_endpoints_local,
    "response_no_credentials":        _check_response_no_credentials,
    "kafka_audit_emitted":            _check_kafka_audit_emitted,
    "safety_critical_has_disclaimer": _check_safety_critical_has_disclaimer,
}


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class ConstitutionViolation:
    rule_id: str
    description: str
    on_violation: str   # "block" | "warn" | "audit_only"
    detail: str


def enforce(response_text: str, state_: dict) -> Tuple[str, List[ConstitutionViolation]]:
    """Evaluate all enabled constitution rules against the final response.

    Returns (response_text, violations).
    Blocking violations replace response_text with a policy-block message.
    Fail-open: exceptions in handlers are caught and logged.
    """
    constitution = _load_constitution()
    rules = constitution.get("constitution") or []
    violations: List[ConstitutionViolation] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        check_name = rule.get("check", "")
        handler = _CHECK_HANDLERS.get(check_name)
        if handler is None:
            logger.debug("⚖️  No handler for constitution check '%s' — skipped", check_name)
            continue
        try:
            detail = handler(response_text, state_)
        except Exception as e:
            logger.warning("⚖️  Constitution check '%s' raised: %s", check_name, e)
            continue
        if detail is None:
            continue

        v = ConstitutionViolation(
            rule_id=rule.get("id", check_name),
            description=rule.get("description", ""),
            on_violation=rule.get("on_violation", "warn"),
            detail=detail,
        )
        violations.append(v)

        if v.on_violation == "block":
            logger.error("⚖️  CONSTITUTION BLOCK [%s]: %s", v.rule_id, v.detail)
        elif v.on_violation != "audit_only":
            logger.warning("⚖️  Constitution warn [%s]: %s", v.rule_id, v.detail)

    # Emit all violations to Kafka audit asynchronously (fire-and-forget)
    if violations:
        try:
            from services.kafka import _kafka_publish
            from config import KAFKA_TOPIC_AUDIT
            _audit_payload = {
                "response_id": state_.get("response_id", ""),
                "ts": time.time(),
                "violations": [
                    {"rule_id": v.rule_id, "on_violation": v.on_violation, "detail": v.detail}
                    for v in violations
                ],
            }
            loop = asyncio.get_running_loop()
            loop.create_task(_kafka_publish(KAFKA_TOPIC_AUDIT, _audit_payload))
        except RuntimeError:
            pass  # No running event loop (e.g. test context) — skip Kafka emit
        except Exception as e:
            logger.debug("⚖️  Kafka audit emit failed: %s", e)

    # Apply blocking rules — replace response with policy message
    blocking = [v for v in violations if v.on_violation == "block"]
    if blocking:
        reasons = ", ".join(v.rule_id for v in blocking)
        response_text = (
            f"[SOVEREIGN POLICY BLOCK] This response was blocked by the system constitution "
            f"({reasons}). Please contact your administrator."
        )

    return response_text, violations
