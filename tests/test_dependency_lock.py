"""Production images must be built from a complete, exact dependency contract."""

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).parents[1]
LOCK = ROOT / "requirements.lock.txt"
MCP_DIRECT = ROOT / "mcp_server" / "requirements.txt"
MCP_LOCK = ROOT / "mcp_server" / "requirements.lock.txt"


def _requirements(path: Path) -> list[Requirement]:
    parsed = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", line)
        parsed.append(Requirement(line))
    return parsed


def test_lock_contains_only_unique_exact_versions():
    requirements = _requirements(LOCK)
    names = [canonicalize_name(item.name) for item in requirements]
    assert len(requirements) >= 100
    assert len(names) == len(set(names))
    for item in requirements:
        specs = list(item.specifier)
        assert len(specs) == 1, item
        assert specs[0].operator == "==", item
        assert "*" not in specs[0].version, item


def test_lock_covers_and_satisfies_every_direct_requirement():
    direct = _requirements(ROOT / "requirements.txt")
    locked = {
        canonicalize_name(item.name): Version(next(iter(item.specifier)).version)
        for item in _requirements(LOCK)
    }
    errors = []
    for requirement in direct:
        name = canonicalize_name(requirement.name)
        if name not in locked:
            errors.append(f"missing {requirement.name}")
        elif requirement.specifier and locked[name] not in requirement.specifier:
            errors.append(
                f"{requirement.name}=={locked[name]} violates {requirement.specifier}"
            )
    assert not errors, "\n".join(errors)


def test_dockerfile_uses_only_lock_and_digest_pinned_python_base():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [
        line for line in dockerfile.splitlines()
        if line.strip().startswith("FROM ")
    ]
    assert len(from_lines) == 2
    assert all(
        "python:3.11-slim@sha256:" in line
        and re.search(r"@sha256:[0-9a-f]{64}\b", line)
        for line in from_lines
    )
    assert "COPY requirements.lock.txt ." in dockerfile
    assert "pip install --prefix=/install -r requirements.lock.txt" in dockerfile
    assert "pip install --prefix=/install -r requirements.txt" not in dockerfile
    assert "python -m pip check" in dockerfile


def test_mcp_lock_is_exact_and_covers_direct_requirements():
    locked_requirements = _requirements(MCP_LOCK)
    locked = {
        canonicalize_name(item.name): Version(next(iter(item.specifier)).version)
        for item in locked_requirements
    }
    assert len(locked_requirements) >= 80
    assert len(locked) == len(locked_requirements)
    assert locked["mcp"] == Version("1.28.1")
    for item in locked_requirements:
        specs = list(item.specifier)
        assert len(specs) == 1, item
        assert specs[0].operator == "==", item

    errors = []
    for requirement in _requirements(MCP_DIRECT):
        name = canonicalize_name(requirement.name)
        if name not in locked:
            errors.append(f"missing {requirement.name}")
        elif requirement.specifier and locked[name] not in requirement.specifier:
            errors.append(
                f"{requirement.name}=={locked[name]} violates {requirement.specifier}"
            )
    assert not errors, "\n".join(errors)


def test_mcp_dockerfile_uses_lock_and_digest_pinned_base():
    dockerfile = (ROOT / "mcp_server" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert "COPY mcp_server/requirements.lock.txt ." in dockerfile
    assert "pip install --no-cache-dir -r requirements.lock.txt" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" not in dockerfile
