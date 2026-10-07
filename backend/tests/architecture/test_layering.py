"""Architecture fitness functions: enforce hexagonal dependency rules on every CI run.

Rule: dependencies point inward. domain <- application <- agents/orchestration <- adapters <- api
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ifap"

# layer -> internal layers it must NOT import
FORBIDDEN: dict[str, set[str]] = {
    "domain": {
        "application",
        "agents",
        "orchestration",
        "adapters",
        "api",
        "config",
        "observability",
    },
    "application": {"agents", "orchestration", "adapters", "api"},
    "agents": {"orchestration", "adapters", "api"},
    "orchestration": {"adapters", "api"},
    "adapters": {"agents", "orchestration", "api"},
}

# Domain may only depend on the standard library and Pydantic.
DOMAIN_ALLOWED_THIRD_PARTY = {"pydantic"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _modules(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_dependencies_point_inward(layer: str) -> None:
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} imports {name}"
        for path in _modules(layer)
        for name in _imports(path)
        if name.startswith("ifap.") and name.split(".")[1] in FORBIDDEN[layer]
    ]
    assert not violations, "\n".join(violations)


def test_domain_is_framework_free() -> None:
    stdlib = sys.stdlib_module_names
    violations = [
        f"{path.name} imports {name}"
        for path in _modules("domain")
        for name in _imports(path)
        if (root := name.split(".")[0]) not in stdlib
        and root not in DOMAIN_ALLOWED_THIRD_PARTY
        and root != "ifap"
    ]
    assert not violations, "\n".join(violations)


def test_only_composition_root_wires_adapters() -> None:
    api_files = [p for p in _modules("api") if p.name != "container.py"]
    violations = [
        f"{path.name} imports {name}"
        for path in api_files
        for name in _imports(path)
        if name.startswith("ifap.adapters")
    ]
    assert not violations, "\n".join(violations)
