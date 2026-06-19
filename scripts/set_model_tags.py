"""One-shot script: set tags on specific models in a named connection for a given user.

Usage (inside moe-admin container or with DB env vars set):
    python scripts/set_model_tags.py

Edits models_cache in-place, preserving all other fields.
Models not present in the cache are skipped with a warning.
"""
import sys
import os
import json
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_ui.database import init_db, close_db, _get_pool

TARGET_USER_NAME = "horndev"
TARGET_CONN_NAME = "openrouterai"

FREE_MODELS = {
    "nvidia/nemotron-3.5-content-safety:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "poolside/laguna-xs.2:free",
    "poolside/laguna-m.1:free",
    "moonshotai/kimi-k2.6:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free",
    "liquid/lfm-2.5-1.2b-thinking:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "z-ai/glm-4.5-air:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
}


async def main() -> None:
    await init_db()
    pool = _get_pool()

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Resolve user_id by name
            await cur.execute("SELECT id FROM users WHERE name=%s OR email=%s",
                              (TARGET_USER_NAME, TARGET_USER_NAME))
            user_row = await cur.fetchone()
            if not user_row:
                print(f"ERROR: User '{TARGET_USER_NAME}' not found")
                return
            user_id = user_row["id"]
            print(f"User: {TARGET_USER_NAME} → {user_id}")

            # Find the connection
            await cur.execute(
                "SELECT id, models_cache FROM user_api_connections "
                "WHERE user_id=%s AND name=%s",
                (user_id, TARGET_CONN_NAME),
            )
            conn_row = await cur.fetchone()
            if not conn_row:
                print(f"ERROR: Connection '{TARGET_CONN_NAME}' not found for user")
                return
            conn_id = conn_row["id"]
            print(f"Connection: {TARGET_CONN_NAME} → {conn_id}")

            try:
                models: list = json.loads(conn_row.get("models_cache") or "[]")
            except Exception:
                models = []
            print(f"Models in cache: {len(models)}")

            updated = 0
            not_found = []
            for m in models:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id", "")
                if mid in FREE_MODELS:
                    existing = set(m.get("tags") or [])
                    if "free" not in existing:
                        existing.add("free")
                        m["tags"] = sorted(existing)
                        updated += 1
                        print(f"  + free → {mid}")
                    else:
                        print(f"  ✓ already tagged → {mid}")

            # Report models from the target list not in cache
            cached_ids = {m.get("id") for m in models if isinstance(m, dict)}
            for mid in sorted(FREE_MODELS):
                if mid not in cached_ids:
                    not_found.append(mid)

            if not_found:
                print(f"\nNot in cache ({len(not_found)}), skipped:")
                for mid in not_found:
                    print(f"  ✗ {mid}")

            if updated == 0:
                print("\nNothing to update.")
                return

            # Persist
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            await cur.execute(
                "UPDATE user_api_connections SET models_cache=%s, updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (json.dumps(models), now, conn_id, user_id),
            )
            print(f"\nUpdated {updated} models. Persisted.")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
