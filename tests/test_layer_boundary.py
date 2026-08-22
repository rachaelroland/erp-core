# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Enforce the open-core boundary.

The core is the open-source candidate; the AI layer is the private one. That
split only survives if it is mechanical — the moment `core` imports `ai`, the
two are welded together and separating them becomes a refactor rather than a
`cp -r`.

Run:  uv run python -m tests.test_layer_boundary
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

def _find_package() -> Path:
    """Locate the package by its SHAPE, not by a hardcoded name.

    This was `parents[1] / "erp"`. Renaming the package to `applied_erp` left
    that pointing at a directory that no longer exists, so `rglob` matched
    nothing and the boundary check passed over an empty set — it reported
    "0 core modules, 0 ai modules" and still very nearly went green. A guard
    that silently inspects nothing is worse than no guard, because it reads as
    a pass. Same class of defect as the CACHE_DIR parent-count bug in docs/16.

    Finding it by looking for the directory that CONTAINS core/ and ai/ means a
    future rename moves the test with it.
    """
    root = Path(__file__).resolve().parents[1]
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "core").is_dir() and (child / "ai").is_dir():
            return child
    raise SystemExit(
        f"FAIL — no package containing both core/ and ai/ found under {root}. "
        "The boundary test cannot inspect anything; refusing to report a pass."
    )


ERP = _find_package()
PKG = ERP.name          # the package name, so a rename cannot desync these checks

# Third-party packages core is permitted to depend on. Kept deliberately
# short: every dependency here is one the open-source release inherits, with
# its licence and its supply chain.
CORE_ALLOWED_THIRD_PARTY = {"psycopg", "psycopg_pool"}

# Packages that are AI-layer only. Core importing any of these means a model
# dependency has leaked into the system of record.
AI_ONLY_THIRD_PARTY = {"openai", "llama_cloud_services", "llama_parse",
                       "anthropic", "tiktoken"}


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Resolve a relative import to a dotted path under erp.
                rel = path.relative_to(ERP).parts[:-1]
                base = list(rel)
                for _ in range(node.level - 1):
                    if base:
                        base.pop()
                target = ".".join(base + ([node.module] if node.module else []))
                found.append((PKG + "." + target if target else PKG, node.lineno))
            elif node.module:
                found.append((node.module, node.lineno))
    return found


def check() -> list[str]:
    failures: list[str] = []

    core_files = sorted((ERP / "core").rglob("*.py"))
    ai_files = sorted((ERP / "ai").rglob("*.py"))
    # Refuse to pass over an empty set. A guard that inspects nothing reports
    # a pass, which is the most dangerous result it can produce.
    if not core_files:
        failures.append(f"no core modules found under {ERP / 'core'} — "
                        "the boundary check would pass vacuously")
    if not ai_files:
        failures.append(f"no AI modules found under {ERP / 'ai'} — "
                        "the boundary check would pass vacuously")

    for path in core_files:
        rel = path.relative_to(ERP.parent)
        for name, line in _imports(path):
            root = name.split(".")[0]

            if name.startswith("applied_erp.ai") or name == "applied_erp.ai":
                failures.append(
                    f"{rel}:{line}  core imports the AI layer ({name}). "
                    f"Core must run with the AI layer absent.")

            if root in AI_ONLY_THIRD_PARTY:
                failures.append(
                    f"{rel}:{line}  core imports {root}, an AI-only dependency. "
                    f"A model dependency in the system of record blocks the "
                    f"open-source split.")

            if (not name.startswith((PKG, ".")) and root not in CORE_ALLOWED_THIRD_PARTY
                    and root not in sys.stdlib_module_names):
                failures.append(
                    f"{rel}:{line}  core depends on third-party '{root}', which "
                    f"is not in CORE_ALLOWED_THIRD_PARTY. Add it deliberately "
                    f"or move the code to the AI layer.")

    # The reverse direction is allowed and expected; assert it actually
    # happens, otherwise the layering is decorative.
    ai_uses_core = any(
        n.startswith("applied_erp.core")
        for path in ai_files
        for n, _ in _imports(path))
    if not ai_uses_core:
        failures.append("no AI module imports applied_erp.core — the layering is not real")

    return failures


def main() -> int:
    problems = check()
    core_files = len(list((ERP / "core").rglob("*.py")))
    ai_files = len(list((ERP / "ai").rglob("*.py")))
    print(f"layer boundary: {core_files} core modules, {ai_files} ai modules")
    if problems:
        print(f"\nFAIL — {len(problems)} violation(s):")
        for p in problems:
            print(f"  {p}")
        return 1
    print("PASS — core does not depend on the AI layer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
