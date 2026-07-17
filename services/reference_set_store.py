"""
services/reference_set_store.py — Persistence for the held-out reference
question set used by scripts/reference_set_regression.py to regression-test
the pipeline's current template/model configuration against known-good
answers, independent of the live-traffic LLM-judge-only A/B sampling in
services/quality_probe.py.
"""

import json
import os
import logging

logger = logging.getLogger("MOE-SOVEREIGN")

# Resolve file path dynamically
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REFERENCE_SET_FILE = os.path.join(DATA_DIR, "reference_questions.json")


def load_reference_set() -> list[dict]:
    """Loads all reference questions from the JSON file."""
    if not os.path.exists(REFERENCE_SET_FILE):
        return []
    try:
        with open(REFERENCE_SET_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
            return items if isinstance(items, list) else []
    except Exception as e:
        logger.error(f"ReferenceSet: Failed to load reference questions from {REFERENCE_SET_FILE}: {e}")
        return []


def save_reference_set(items: list[dict]) -> None:
    """Saves reference questions to the JSON file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(REFERENCE_SET_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        logger.info(f"ReferenceSet: Saved {len(items)} reference questions to {REFERENCE_SET_FILE}.")
    except Exception as e:
        logger.error(f"ReferenceSet: Failed to save reference questions to {REFERENCE_SET_FILE}: {e}")
