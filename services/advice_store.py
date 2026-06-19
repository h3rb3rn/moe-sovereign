"""
services/advice_store.py — Persistence and retrieval for John McCarthy's Advice Taker rules.
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

# Resolve file path dynamically
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ADVICE_FILE = os.path.join(DATA_DIR, "declarative_advice.json")

def load_advice_rules() -> list[dict]:
    """Loads all declarative advice rules from the JSON file."""
    if not os.path.exists(ADVICE_FILE):
        return []
    try:
        with open(ADVICE_FILE, "r", encoding="utf-8") as f:
            rules = json.load(f)
            return rules if isinstance(rules, list) else []
    except Exception as e:
        logger.error(f"AdviceTaker: Failed to load advice rules from {ADVICE_FILE}: {e}")
        return []

def save_advice_rules(rules: list[dict]) -> None:
    """Saves declarative advice rules to the JSON file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ADVICE_FILE, "w", encoding="utf-8") as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)
        logger.info(f"AdviceTaker: Saved {len(rules)} advice rules to {ADVICE_FILE}.")
    except Exception as e:
        logger.error(f"AdviceTaker: Failed to save advice rules to {ADVICE_FILE}: {e}")

def add_advice_rule(rule_text: str, category_scope: str = "all", enabled: bool = True) -> dict:
    """Adds a new advice rule."""
    import secrets
    rules = load_advice_rules()
    new_rule = {
        "id": f"advice-{secrets.token_hex(4)}",
        "rule": rule_text.strip(),
        "category_scope": category_scope.strip().lower(),
        "enabled": enabled
    }
    rules.append(new_rule)
    save_advice_rules(rules)
    return new_rule

def delete_advice_rule(rule_id: str) -> bool:
    """Deletes an advice rule by ID."""
    rules = load_advice_rules()
    filtered = [r for r in rules if r.get("id") != rule_id]
    if len(filtered) < len(rules):
        save_advice_rules(filtered)
        return True
    return False

def get_active_advice(query: str = None) -> list[str]:
    """Returns all active advice rules as a list of strings."""
    rules = load_advice_rules()
    active = []
    for r in rules:
        if not r.get("enabled", True):
            continue
            
        # Match scopes
        scope = r.get("category_scope", "all")
        if scope == "all":
            active.append(r["rule"])
        elif query and scope in query.lower():
            active.append(r["rule"])
            
    return active
