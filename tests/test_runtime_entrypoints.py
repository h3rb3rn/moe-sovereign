"""Machine-checkable contracts for name-based framework callbacks."""

import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "configs" / "runtime_entrypoints.json"


def _qualified_definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions: set[str] = set()

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = parents + (child.name,)
                definitions.add(".".join(qualified))
                visit(child, qualified)
            else:
                visit(child, parents)

    visit(tree)
    return definitions


def test_dynamic_entrypoint_manifest_is_complete_and_wired():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = payload["entrypoints"]
    assert payload["schema_version"] == 1
    assert len(entries) == 9
    assert len({(entry["path"], entry["qualname"]) for entry in entries}) == len(entries)

    errors = []
    for entry in entries:
        source = ROOT / entry["path"]
        wiring = ROOT / entry["wiring_path"]
        if not source.exists():
            errors.append(f"missing source: {entry['path']}")
            continue
        if entry["qualname"] not in _qualified_definitions(source):
            errors.append(f"missing definition: {entry['path']}::{entry['qualname']}")
        if not wiring.exists():
            errors.append(f"missing wiring source: {entry['wiring_path']}")
        elif entry["wiring_marker"] not in wiring.read_text(encoding="utf-8"):
            errors.append(
                f"missing wiring marker: {entry['wiring_path']}::{entry['wiring_marker']}"
            )
        if not entry["kind"].strip():
            errors.append(f"missing callback kind: {entry['qualname']}")

    assert not errors, "\n".join(errors)
