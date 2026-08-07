"""Regression contract for the TASK-35/TASK-36 zero-reference inventory."""

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]

REMOVED_DEFINITIONS = {
    "admin_ui/app.py": {"_prom_range", "_get_global_server_names"},
    "admin_ui/database.py": {
        "log_usage",
        "get_admin_template",
        "delete_admin_template",
    },
    "admin_ui/maintenance.py": {"_set_run_status"},
    "mission_context.py": {"append_decision"},
    "pipeline/logic_types.py": {"ConstructiveProof", "assert_proven"},
    "scripts/gap_healer_templates.py": {"_get_avg_duration", "_record_timing"},
    "scripts/index_models_metadata.py": {"fetch_local_models"},
    "scripts/train_judge_lora.py": {"format_prompt"},
    "services/context_index.py": {"retrieve"},
    "services/reference_set_store.py": {"save_reference_set"},
}

REQUIRED_WIRING = {
    "admin_ui/app.py": {
        "db.get_federation_policy(",
        "db.create_outbox_entry(",
        "db.get_outbox_entry(",
        "db.update_outbox_status(",
        "db.get_tenant(",
        "get_manual_domains(",
        "client.handshake(",
    },
    "routes/admin_rlsf.py": {"if not is_enabled():"},
    "services/rlsf_local_loop.py": {"if not is_enabled():"},
    "routes/watchdog.py": {"_starfleet.set_feature_enabled("},
}


def _definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_confirmed_dead_or_misleading_definitions_stay_removed():
    errors = []
    for relative, forbidden in REMOVED_DEFINITIONS.items():
        remaining = forbidden & _definitions(ROOT / relative)
        if remaining:
            errors.append(f"{relative}: {sorted(remaining)}")
    assert not errors, "\n".join(errors)
    assert not (ROOT / "federation" / "sync.py").exists()


def test_retained_half_wired_functions_have_real_execution_sites():
    errors = []
    for relative, markers in REQUIRED_WIRING.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        missing = markers - {marker for marker in markers if marker in source}
        if missing:
            errors.append(f"{relative}: {sorted(missing)}")
    assert not errors, "\n".join(errors)
