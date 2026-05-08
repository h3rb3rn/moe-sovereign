"""
Integration tests verifying that lineage hook points exist in pipeline modules.

These are source-scan tests — we don't execute the full pipeline (heavy mocking
required). Instead we verify each hook is present and well-formed: the module
calls start_run + complete_run/fail_run for the expected job name.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (_ROOT / rel_path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rel_path,job_name",
    [
        ("services/pipeline/chat.py",     "chat_completion"),
        ("services/pipeline/anthropic.py", "anthropic_messages"),
        ("services/pipeline/responses.py", "responses_api"),
        ("graph/synthesis.py",             "merger_node"),
        ("main.py",                         "kafka_ingest"),
    ],
)
def test_module_emits_lineage_for_job(rel_path: str, job_name: str):
    """Each pipeline entry-point or sub-job module must START + (COMPLETE or FAIL) the job."""
    src = _read(rel_path)
    assert "from services.lineage import" in src, \
        f"{rel_path} does not import services.lineage"
    assert f'"{job_name}"' in src, \
        f"{rel_path} does not reference job name {job_name!r}"
    assert "start_run" in src and ("complete_run" in src or "fail_run" in src), \
        f"{rel_path} missing start_run + complete/fail emission for {job_name}"


def test_lineage_module_is_self_contained():
    """services/lineage.py must not import from main or other route handlers."""
    src = _read("services/lineage.py")
    assert "from main" not in src and "import main" not in src, \
        "lineage module must not depend on main.py (would create import cycle)"
    assert "from routes" not in src, \
        "lineage module must not depend on routes/ (it sits below them)"
