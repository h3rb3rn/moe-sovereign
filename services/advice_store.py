"""
services/advice_store.py — Persistence, retrieval, and enforcement for John McCarthy's Advice Taker rules.
"""

import json
import os
import re
import logging

logger = logging.getLogger("MOE-SOVEREIGN")

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

def add_advice_rule(
    rule_text: str,
    category_scope: str = "all",
    pattern: str = "",
    category: str = "",
    mcp_tool: str = "",
    default_task_description: str = "",
    enabled: bool = True
) -> dict:
    """Adds a new advice rule with rich constraints."""
    import secrets
    rules = load_advice_rules()
    new_rule = {
        "id": f"advice-{secrets.token_hex(4)}",
        "rule": rule_text.strip(),
        "category_scope": category_scope.strip().lower(),
        "pattern": pattern.strip(),
        "category": category.strip(),
        "mcp_tool": mcp_tool.strip(),
        "default_task_description": default_task_description.strip(),
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
    """Returns all active advice rules matching the query as a list of strings."""
    rules = load_advice_rules()
    active = []
    for r in rules:
        if not r.get("enabled", True):
            continue
            
        # Match using pattern or scope
        matched = False
        pattern = r.get("pattern", "")
        if pattern and query:
            try:
                if re.search(pattern, query, re.I):
                    matched = True
            except Exception as e:
                logger.error(f"AdviceTaker: Regex error on pattern '{pattern}': {e}")
        else:
            scope = r.get("category_scope", "all")
            if scope == "all":
                matched = True
            elif query and scope in query.lower():
                matched = True
                
        if matched:
            active.append(r["rule"])
            
    return active

def enforce_advice_rules(query: str, plan: list) -> list:
    """Enforces symbolic constraints on the generated plan based on declarative advice rules."""
    rules = load_advice_rules()
    modified_plan = list(plan)
    
    for r in rules:
        if not r.get("enabled", True):
            continue
            
        pattern = r.get("pattern", "")
        mcp_tool = r.get("mcp_tool", "")
        category = r.get("category", "")
        
        # Match using regex or substring
        matched = False
        if pattern:
            try:
                if re.search(pattern, query, re.I):
                    matched = True
            except Exception:
                pass
        else:
            scope = r.get("category_scope", "all")
            if scope == "all" or (query and scope in query.lower()):
                matched = True
                
        if matched:
            # Enforce rule: check if any task in plan matches category and/or mcp_tool
            # If not, inject the required task!
            has_match = False
            for t in modified_plan:
                if category and t.get("category") != category:
                    continue
                if mcp_tool and t.get("mcp_tool") != mcp_tool:
                    continue
                has_match = True
                break
                
            if not has_match:
                logger.info(f"🎯 AdviceTaker: Rule {r.get('id')} ('{r['rule']}') matched query. Enforcing task constraint...")
                new_task = {
                    "task": r.get("default_task_description") or f"Enforced task based on advice: {r['rule']}",
                    "category": category or "general"
                }
                if mcp_tool:
                    new_task["mcp_tool"] = mcp_tool
                    # Extract parameter values based on mcp_tool type
                    if mcp_tool == "legal_get_paragraph":
                        law_match = re.search(r'§\s*(\d+\w*)\s*(bgb|stgb|gg|hgb)', query, re.I)
                        if law_match:
                            new_task["mcp_args"] = {"law": law_match.group(2).upper(), "paragraph": law_match.group(1)}
                        else:
                            new_task["mcp_args"] = {"law": "BGB", "paragraph": "242"}
                    elif mcp_tool == "subnet_calc":
                        cidr_match = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b', query)
                        if cidr_match:
                            new_task["mcp_args"] = {"cidr": cidr_match.group(0)}
                    else:
                        new_task["mcp_args"] = {}
                            
                # Inject at the beginning of the plan so it runs before general experts
                modified_plan.insert(0, new_task)
                
    return modified_plan
