from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "arca"
TESTS = Path(__file__).resolve().parent
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ENTRY_POINTS = {"main", "create_app"}
PLANNED = {
    "pipeline.scope_titles": (
        "reads the curated article list of deep_index_scope; the ZIM deep indexing that "
        "consumes it is not built yet, so the panel records the choice and nothing acts on "
        "it. Delete this entry the day that indexing lands, or delete the function."
    ),
}


def _module_sources() -> dict[str, str]:
    return {path.stem: path.read_text("utf-8") for path in sorted(PACKAGE.glob("*.py"))}


def _public_definitions(sources: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for module, text in sources.items():
        tree = ast.parse(text)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("_") and node.name not in ENTRY_POINTS:
                found.setdefault(node.name, module)
    return found


def _referenced_names(text: str) -> set[str]:
    tree = ast.parse(text)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _uses_outside(name: str, home: str, sources: dict[str, str]) -> bool:
    for module, text in sources.items():
        if module == home:
            continue
        if name in _referenced_names(text):
            return True
    for extra in SCRIPTS.glob("*.py"):
        if name in _referenced_names(extra.read_text("utf-8")):
            return True
    return False


def _used_at_home(name: str, home: str, sources: dict[str, str]) -> bool:
    tree = ast.parse(sources[home])
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            continue
        if isinstance(node, ast.Name) and node.id == name:
            return True
    return False


def test_nothing_in_the_package_lives_only_for_the_tests() -> None:
    sources = _module_sources()
    definitions = _public_definitions(sources)
    orphans = sorted(
        f"{home}.{name}"
        for name, home in definitions.items()
        if not _uses_outside(name, home, sources)
        and not _used_at_home(name, home, sources)
        and f"{home}.{name}" not in PLANNED
    )
    assert not orphans, (
        "these public functions have no caller outside tests/, which means the suite covers "
        f"code the product never runs: {orphans}"
    )


def test_the_test_suite_reaches_every_module_of_the_package() -> None:
    covered = " ".join(path.read_text("utf-8") for path in TESTS.glob("test_*.py"))
    missing = [
        path.stem
        for path in sorted(PACKAGE.glob("*.py"))
        if path.stem != "__init__" and f"arca.{path.stem}" not in covered
    ]
    assert not missing, missing


def test_every_planned_orphan_is_still_an_orphan() -> None:
    sources = _module_sources()
    definitions = _public_definitions(sources)
    for entry, reason in PLANNED.items():
        home, _, name = entry.partition(".")
        assert definitions.get(name) == home, f"{entry} no longer exists: drop it from PLANNED"
        assert not _uses_outside(name, home, sources), (
            f"{entry} now has a caller, so it is no longer planned work: drop it from PLANNED"
        )
        assert len(reason) > 40, entry
