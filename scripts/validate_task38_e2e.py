#!/usr/bin/env python3
"""End-to-end acceptance probe for TASK-38.

Run this script inside the ``langgraph-orchestrator`` container.  It creates a
short-lived API key for the selected user, runs the same deterministic prompt
against the native model and the user's expert template, and revokes the key in
a ``finally`` block.  The raw key is never printed or written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin_ui import database as db


PROMPT = """\
Bearbeite alle vier Teilaufgaben und prüfe jedes Ergebnis:
1. Berechne den größten gemeinsamen Teiler von 391 und 299.
2. Rechne 72 km/h exakt in m/s um.
3. Bestimme den deutschen Wochentag für den 29.07.2026.
4. Prüfe diese SQLite-Zeile auf SQL-Injection und gib eine sichere,
   parametrisierte cursor.execute-Ersatzzeile an:
   cursor.execute("SELECT * FROM students WHERE name = '" + user_input + "'")

Antworte ausschließlich mit einem validen JSON-Objekt und exakt diesen sechs
Feldern: "gcd" (Ganzzahl), "gcd_proof" (kurzer Prüfnachweis),
"speed_m_s" (Zahl), "weekday_de" (String), "sql_injection" (Boolean) und
"safe_execute" (String). Keine Markdown-Codefences und keine weiteren Felder.
"""

EXPECTED_FIELDS = {
    "gcd",
    "gcd_proof",
    "speed_m_s",
    "weekday_de",
    "sql_injection",
    "safe_execute",
}


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("response is not a JSON object")
    return value


def _score_payload(payload: dict[str, Any]) -> dict[str, bool]:
    safe_execute = str(payload.get("safe_execute", ""))
    proof = str(payload.get("gcd_proof", ""))
    return {
        "exact_fields": set(payload) == EXPECTED_FIELDS,
        "gcd": payload.get("gcd") == 23,
        "gcd_proof": "391" in proof and "299" in proof and "23" in proof,
        "speed_m_s": payload.get("speed_m_s") in (20, 20.0),
        "weekday_de": str(payload.get("weekday_de", "")).casefold() == "mittwoch",
        "sql_injection": payload.get("sql_injection") is True,
        "safe_execute": (
            "cursor.execute" in safe_execute
            and "user_input" in safe_execute
            and ("?" in safe_execute or "%s" in safe_execute)
        ),
    }


async def _run_once(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    run_number: int,
) -> dict[str, Any]:
    started = time.monotonic()
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0,
            "max_tokens": 1200,
            "no_cache": True,
            "response_format": {"type": "json_object"},
        },
    )
    latency_s = time.monotonic() - started
    request_id = response.headers.get("x-request-id", "")
    result: dict[str, Any] = {
        "model": model,
        "run": run_number,
        "http_status": response.status_code,
        "latency_s": round(latency_s, 3),
        "request_id": request_id,
        "usage": {},
        "valid_json": False,
        "checks": {},
        "error": "",
    }
    try:
        body = response.json()
    except ValueError:
        result["error"] = "non_json_http_response"
        return result

    if not request_id:
        result["request_id"] = str(body.get("id", ""))
    result["usage"] = body.get("usage", {})
    if response.status_code != 200:
        detail = body.get("detail", body.get("error", "http_error"))
        result["error"] = str(detail)[:500]
        return result

    try:
        payload = _extract_json(body["choices"][0]["message"]["content"])
        result["valid_json"] = True
        result["checks"] = _score_payload(payload)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result["error"] = f"invalid_model_payload:{type(exc).__name__}:{exc}"
    return result


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["latency_s"]) for row in results]
    return {
        "runs": len(results),
        "http_200": sum(row["http_status"] == 200 for row in results),
        "valid_json": sum(bool(row["valid_json"]) for row in results),
        "all_checks_passed": sum(
            bool(row["checks"]) and all(row["checks"].values()) for row in results
        ),
        "latency_min_s": min(latencies) if latencies else None,
        "latency_median_s": (
            round(statistics.median(latencies), 3) if latencies else None
        ),
        "latency_max_s": max(latencies) if latencies else None,
    }


async def _main(args: argparse.Namespace) -> int:
    key_id = ""
    user_id = ""
    raw_key = ""
    results: list[dict[str, Any]] = []
    await db.init_db()
    try:
        user = await db.get_user_by_username(args.username)
        if not user or not user.get("is_active"):
            raise RuntimeError(f"active user not found: {args.username}")
        user_id = str(user["id"])
        templates = await db.list_user_templates(user_id)
        matching = [
            template
            for template in templates
            if template.get("name") == args.template and template.get("is_active", True)
        ]
        if len(matching) != 1:
            raise RuntimeError(
                f"expected one active user template named {args.template!r}, "
                f"found {len(matching)}"
            )

        raw_key, key_record = await db.create_api_key(
            user_id, "TASK-38 temporary E2E validation"
        )
        key_id = str(key_record["id"])
        await db.sync_user_to_redis(user_id)

        timeout = httpx.Timeout(args.timeout_seconds)
        selected_models = {
            "native": (args.native_model,),
            "template": (args.template,),
            "both": (args.native_model, args.template),
        }[args.target]
        async with httpx.AsyncClient(
            base_url=args.base_url, timeout=timeout
        ) as client:
            for model in selected_models:
                for run_number in range(1, args.runs + 1):
                    row = await _run_once(
                        client,
                        api_key=raw_key,
                        model=model,
                        run_number=run_number,
                    )
                    results.append(row)
                    print(json.dumps({"result": row}, ensure_ascii=False), flush=True)

        by_model = {
            model: _summary([row for row in results if row["model"] == model])
            for model in selected_models
        }
        print(
            json.dumps(
                {
                    "summary": by_model,
                    "temporary_key_persisted": False,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if all(
            row["http_status"] == 200
            and row["valid_json"]
            and row["checks"]
            and all(row["checks"].values())
            for row in results
        ) else 1
    finally:
        raw_key = ""
        if key_id:
            key_hash = await db.revoke_api_key(key_id)
            if key_hash:
                await db.invalidate_api_key_redis(key_hash)
        if user_id:
            await db.sync_user_to_redis(user_id)
        await db.close_db()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="horndev")
    parser.add_argument("--native-model", default="qwen3.6:35b@N04-RTX")
    parser.add_argument("--template", default="moe-n04-rtx-qwen3.6:35b-256k")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--target",
        choices=("native", "template", "both"),
        default="both",
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
