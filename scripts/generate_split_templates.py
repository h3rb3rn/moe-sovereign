"""Bulk-generate split-instance curator templates (one-off migration helper).

Delegates all cloning logic to admin_ui.curator_provisioner so the script and
the live admin toggle stay in sync. Kept for historical reproducibility —
new servers should flow through the UI checkbox instead.
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from admin_ui import database  # noqa: E402
from admin_ui import curator_provisioner as cp  # noqa: E402


# Model assignments for the original split. Keep these pinned for
# whitepaper reproducibility even though the live UI chooses models
# automatically from VRAM now.
MODEL_ASSIGNMENTS = {
    "N06-M10": {
        "01": "mistral-nemo:latest",
        "02": "glm4:9b",
        "03": "llama3.1:8b",
        "04": "hermes3:8b",
    },
    "N11-M10": {
        "01": "mistral:7b",
        "02": "glm4:9b",
        "03": "llama3.1:8b",
        "04": "qwen2.5:7b",
    },
}


async def main() -> None:
    await database.init_db()
    existing = await database.list_admin_templates()
    by_name = {t["name"]: t for t in existing}

    created = 0
    for parent_node, slots in MODEL_ASSIGNMENTS.items():
        parent = by_name.get(f"moe-ontology-curator-{parent_node.lower()}")
        if parent is None:
            print(f"ERROR: parent template for {parent_node} not found", flush=True)
            continue
        for slot, model in slots.items():
            new_node = f"{parent_node}-{slot}"
            variant = cp.build_curator_template(
                parent, new_node=new_node, new_model=model, parent_node=parent_node,
            )
            if variant["name"] in by_name:
                print(f"  - {variant['name']}: exists, upserting", flush=True)
            else:
                print(f"  + {variant['name']} -> {model}@{new_node}", flush=True)
            await database.upsert_admin_template(variant)
            created += 1

    print(f"\nUpserted {created} templates.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
