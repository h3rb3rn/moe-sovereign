#!/usr/bin/env python3
"""Cross-facade E2E probe for TASK-46 with an ephemeral user key.

Run inside ``langgraph-orchestrator``. The raw key is retained only in memory;
the finally block revokes, invalidates and archives its database record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin_ui import database as db


PURE_PROMPT = (
    "Bestimme die Zeitfakten für 2026-07-29T12:00:00Z in Europe/Berlin."
)
MIXED_PROMPT = """\
1. Konvertiere 2026-07-29T12:00:00 von UTC nach Europe/Berlin.
2. Prüfe diese SQLite-Zeile auf SQL-Injection und gib eine sichere,
   parametrisierte cursor.execute-Ersatzzeile an:
   cursor.execute("SELECT * FROM students WHERE name = '" + user_input + "'")
"""


def _request(facade: str, model: str, prompt: str) -> tuple[str, dict[str, Any]]:
    if facade == "chat":
        return "/v1/chat/completions", {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 1200,
            "no_cache": True,
        }
    if facade == "responses":
        return "/v1/responses", {
            "model": model,
            "input": prompt,
            "temperature": 0,
            "max_output_tokens": 1200,
            "no_cache": True,
        }
    return "/v1/messages", {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
        "no_cache": True,
    }


def _content(facade: str, body: dict[str, Any]) -> str:
    if facade == "chat":
        return str(body["choices"][0]["message"]["content"])
    if facade == "responses":
        return "".join(
            str(block.get("text") or "")
            for item in body.get("output") or []
            for block in item.get("content") or []
            if isinstance(block, dict) and block.get("type") == "output_text"
        )
    return "".join(
        str(block.get("text") or "")
        for block in body.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _checks(kind: str, content: str) -> dict[str, bool]:
    common = {
        "no_opaque_marker": "[[MOE_PRECISION:" not in content,
        "no_duplicate_source_time": content.count("2026-07-29T12:00:00") == 1,
    }
    if kind == "pure":
        return {
            **common,
            "target_time": "2026-07-29T14:00:00+02:00" in content,
            "weekday": "Mittwoch" in content,
        }
    return {
        **common,
        "target_time": "2026-07-29T14:00:00+02:00" in content,
        "safe_execute": (
            "cursor.execute" in content
            and "user_input" in content
            and ("?" in content or "%s" in content)
        ),
    }


async def _main(args: argparse.Namespace) -> int:
    key_id = ""
    user_id = ""
    raw_key = ""
    rows: list[dict[str, Any]] = []
    await db.init_db()
    try:
        user = await db.get_user_by_username(args.username)
        if not user or not user.get("is_active"):
            raise RuntimeError(f"active user not found: {args.username}")
        user_id = str(user["id"])
        templates = await db.list_user_templates(user_id)
        if sum(
            item.get("name") == args.template and item.get("is_active", True)
            for item in templates
        ) != 1:
            raise RuntimeError(f"active user template not found: {args.template}")
        raw_key, key_record = await db.create_api_key(
            user_id, "TASK-46 temporary cross-facade validation"
        )
        key_id = str(key_record["id"])
        await db.sync_user_to_redis(user_id)

        prompt = PURE_PROMPT if args.kind == "pure" else MIXED_PROMPT
        timeout = httpx.Timeout(args.timeout_seconds)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
            for facade in args.facades:
                path, payload = _request(facade, args.template, prompt)
                started = time.monotonic()
                response = await client.post(
                    path,
                    headers={"Authorization": f"Bearer {raw_key}"},
                    json=payload,
                )
                latency = round(time.monotonic() - started, 3)
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                content = _content(facade, body) if response.status_code == 200 else ""
                row = {
                    "facade": facade,
                    "http_status": response.status_code,
                    "latency_s": latency,
                    "request_id": str(body.get("id") or response.headers.get("x-request-id") or ""),
                    "usage": body.get("usage") or {},
                    "checks": _checks(args.kind, content) if content else {},
                    "content": content,
                    "error": "" if response.status_code == 200 else str(
                        body.get("detail") or body.get("error") or "http_error"
                    )[:500],
                }
                rows.append(row)
                print(json.dumps({"result": row}, ensure_ascii=False), flush=True)
        passed = all(
            row["http_status"] == 200
            and row["checks"]
            and all(row["checks"].values())
            for row in rows
        )
        print(json.dumps({
            "summary": {
                "kind": args.kind,
                "facades": len(rows),
                "passed": passed,
                "temporary_key_persisted": False,
            }
        }, ensure_ascii=False), flush=True)
        return 0 if passed else 1
    finally:
        raw_key = ""
        if key_id:
            key_hash = await db.revoke_api_key(key_id)
            if key_hash:
                await db.invalidate_api_key_redis(key_hash)
        if user_id:
            if key_id:
                await db.archive_api_key(key_id, user_id)
            await db.sync_user_to_redis(user_id)
        await db.close_db()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="horndev")
    parser.add_argument("--template", default="moe-n04-rtx-qwen3.6:35b-256k")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kind", choices=("pure", "mixed"), default="mixed")
    parser.add_argument(
        "--facades", nargs="+", choices=("chat", "responses", "anthropic"),
        default=("chat", "responses", "anthropic"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
