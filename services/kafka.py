"""
services/kafka.py — Kafka publish helper.

_kafka_publish is the single authoritative fire-and-forget wrapper used by:
  - main.py  (consumer loop, shadow requests, feedback, federation, etc.)
  - routes/graph.py  (knowledge import/export events)

All callers use:  from services.kafka import _kafka_publish
"""

import json
import logging

import state

logger = logging.getLogger("MOE-SOVEREIGN")


async def _kafka_publish(topic: str, payload: dict) -> None:
    """Send a JSON message to a Kafka topic. Silently no-ops when unavailable."""
    if state.kafka_producer is None:
        return
    try:
        data = json.dumps(payload).encode()
        await state.kafka_producer.send_and_wait(topic, data)
    except Exception as e:
        logger.warning("Kafka publish [%s] failed: %s", topic, e)
