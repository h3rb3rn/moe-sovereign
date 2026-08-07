#!/usr/bin/env python3
"""TASK-50 live precision benchmark with an ephemeral horndev API key.

Run inside ``langgraph-orchestrator``. The native leg calls the configured
N04-RTX backend directly so the public precision preflight cannot turn the
native baseline into an orchestrated result. The raw key and backend token
remain memory-only; the key is revoked, invalidated and archived in finally.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from admin_ui import database as db


CORPUS_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/precision_contract_corpus_v1.json"


def _request(facade: str, model: str, prompt: str) -> tuple[str, dict[str, Any]]:
    common = {"model": model, "temperature": 0, "no_cache": True}
    if facade == "chat":
        return "/v1/chat/completions", {
            **common, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1400,
        }
    if facade == "responses":
        return "/v1/responses", {**common, "input": prompt, "max_output_tokens": 1400}
    return "/v1/messages", {
        **common, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1400, "stream": False,
    }


def _content(facade: str, body: dict[str, Any]) -> str:
    if facade == "chat":
        return str(body.get("choices", [{}])[0].get("message", {}).get("content") or "")
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


def _summary(label: str, response: httpx.Response, latency: float, content: str, expected: list[str]) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "label": label,
        "http_status": response.status_code,
        "latency_s": round(latency, 3),
        "request_id": str(body.get("id") or response.headers.get("x-request-id") or ""),
        "usage": body.get("usage") or {},
        "checks": {value: value in content for value in expected},
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_excerpt": content[:1600],
    }


async def _post(client: httpx.AsyncClient, path: str, *, headers: dict[str, str], payload: dict[str, Any]) -> tuple[httpx.Response, float]:
    started = time.monotonic()
    response = await client.post(path, headers=headers, json=payload)
    return response, time.monotonic() - started


async def _main(args: argparse.Namespace) -> int:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    raw_key = ""
    key_id = ""
    user_id = ""
    results: list[dict[str, Any]] = []
    await db.init_db()
    try:
        user = await db.get_user_by_username(args.username)
        if not user or not user.get("is_active"):
            raise RuntimeError(f"active user not found: {args.username}")
        user_id = str(user["id"])
        templates = await db.list_user_templates(user_id)
        if sum(item.get("name") == args.template and item.get("is_active", True) for item in templates) != 1:
            raise RuntimeError(f"active template not found: {args.template}")
        raw_key, record = await db.create_api_key(user_id, "TASK-50 temporary precision rollout benchmark")
        key_id = str(record["id"])
        await db.sync_user_to_redis(user_id)
        auth = {"Authorization": f"Bearer {raw_key}"}
        timeout = httpx.Timeout(args.timeout_seconds)

        async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
            if args.api_matrix:
                for case in corpus["positive"]:
                    for facade in ("chat", "responses", "anthropic"):
                        path, payload = _request(facade, args.template, case["prompt"])
                        response, latency = await _post(client, path, headers=auth, payload=payload)
                        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                        content = _content(facade, body) if response.status_code == 200 else ""
                        row = _summary(f"{case['id']}:{facade}", response, latency, content, case["expected"])
                        results.append(row)
                        print(json.dumps({"result": row}, ensure_ascii=False), flush=True)
                mixed = corpus["mixed"][0]
                path, payload = _request("chat", args.template, mixed["prompt"])
                response, latency = await _post(client, path, headers=auth, payload=payload)
                body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                content = _content("chat", body) if response.status_code == 200 else ""
                row = _summary("mixed:chat", response, latency, content, mixed["expected"])
                results.append(row)
                print(json.dumps({"result": row}, ensure_ascii=False), flush=True)

            if args.native_compare:
                case = next(item for item in corpus["positive"] if item["id"] == "finance-percentage-de")
                server = next(item for item in config.INFERENCE_SERVERS_LIST if item.get("name") == "N04-RTX")
                backend_headers = {"content-type": "application/json"}
                backend_token = str(server.get("token") or "")
                if backend_token:
                    backend_headers["Authorization"] = f"Bearer {backend_token}"
                async with httpx.AsyncClient(base_url=str(server["url"]).rstrip("/"), timeout=timeout) as backend:
                    for run in ("first", "warm"):
                        response, latency = await _post(
                            backend, "/chat/completions", headers=backend_headers,
                            payload={
                                "model": args.native_model,
                                "messages": [{"role": "user", "content": case["prompt"]}],
                                "temperature": 0, "max_tokens": 1400,
                            },
                        )
                        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                        content = str(body.get("choices", [{}])[0].get("message", {}).get("content") or "")
                        row = _summary(f"native:{run}", response, latency, content, case["expected"][:1])
                        results.append(row)
                        print(json.dumps({"result": row}, ensure_ascii=False), flush=True)
                for run in ("first", "warm"):
                    path, payload = _request("chat", args.template, case["prompt"])
                    response, latency = await _post(client, path, headers=auth, payload=payload)
                    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    content = _content("chat", body) if response.status_code == 200 else ""
                    row = _summary(f"orchestrated:{run}", response, latency, content, case["expected"])
                    results.append(row)
                    print(json.dumps({"result": row}, ensure_ascii=False), flush=True)

        passed = all(
            row["http_status"] == 200 and row["checks"] and all(row["checks"].values())
            for row in results
        )
        print(json.dumps({
            "summary": {
                "corpus_id": corpus["corpus_id"], "results": len(results),
                "passed": passed, "timeout_seconds": args.timeout_seconds,
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


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="horndev")
    parser.add_argument("--template", default="moe-n04-rtx-qwen3.6:35b-256k")
    parser.add_argument("--native-model", default="qwen3.6:35b")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--api-matrix", action="store_true")
    parser.add_argument("--native-compare", action="store_true")
    args = parser.parse_args()
    if not args.api_matrix and not args.native_compare:
        args.api_matrix = args.native_compare = True
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_args())))
