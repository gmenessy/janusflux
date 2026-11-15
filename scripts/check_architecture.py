"""Statischer Architektur-Linter (LWP-01):
Prüft:
1. Keine verbotenen Imports (vllm, sglang, torch, langchain, langgraph, litellm, redis, celery).
2. Keine dynamische Code-Ausführung über eval() oder exec().
3. Alle ctx.*-Aufrufe in workflows/ für Phasen-/Skill-Operationen (step, detect_skill, skill, detect_and_run_skill)
   tragen explizit die Keyword-Argumente stufe=... und begruendung=...
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IMPORTS: set[str] = {
    "vllm",
    "sglang",
    "torch",
    "langchain",
    " langchain_core",
    "langgraph",
    "litellm",
    "redis",
    "celery",
    "qdrant_client",
}

FORBIDDEN_BUILTINS: set[str] = {"eval", "exec"}

CTX_GATED_METHODS: set[str] = {
    "step",
    "detect_skill",
    "skill",
    "detect_and_run_skill",
    "decide",
    "rule",
    "generate",
}


def check_source_code(source: str, filename: str = "<string>", is_workflow: bool = False) -> list[str]:
    """Prüft einen Python-Quelltext per AST auf Architekturverstöße."""
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"{filename}:{exc.lineno}: SyntaxError: {exc.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                if root_pkg in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"{filename}:{node.lineno}: Verbotener Import '{alias.name}' (Architektur-Verbot)."
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                if root_pkg in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"{filename}:{node.lineno}: Verbotener Import 'from {node.module}' (Architektur-Verbot)."
                    )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BUILTINS:
                errors.append(
                    f"{filename}:{node.lineno}: Verbotener Aufruf '{node.func.id}()' (Sicherheits-Verbot)."
                )
            if (
                is_workflow
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctx"
                and node.func.attr in CTX_GATED_METHODS
            ):
                kw_names = {kw.arg for kw in node.keywords if kw.arg is not None}
                missing = {"stufe", "begruendung"} - kw_names
                if missing:
                    errors.append(
                        f"{filename}:{node.lineno}: Aufruf 'ctx.{node.func.attr}()' im Workflow "
                        f"fehlt Pflicht-Keyword-Argument(e): {sorted(missing)}"
                    )

    return errors


def check_directories(root_dir: Path) -> list[str]:
    errors: list[str] = []
    for sub, is_wf in [("src", False), ("workflows", True), ("scripts", False)]:
        target = root_dir / sub
        if not target.exists():
            continue
        for py_file in sorted(target.rglob("*.py")):
            if py_file.name == "check_architecture.py":
                continue
            src = py_file.read_text(encoding="utf-8")
            errors.extend(check_source_code(src, filename=str(py_file), is_workflow=is_wf))
    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else Path(".")
    errors = check_directories(root)
    if errors:
        for err in errors:
            print(f"[ARCH-ERROR] {err}", file=sys.stderr)
        return 1
    print("[ARCH-OK] Alle Architektur- und Stufenregeln eingehalten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
