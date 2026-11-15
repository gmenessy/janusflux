"""Abnahmetests für LWP-01: Projekt-Gerüst, Architektur-Linter & Log-Scanner."""

from pathlib import Path

from scripts.check_architecture import check_directories, check_source_code
from scripts.scan_cleartext_logs import scan_jsonl_line, scan_logs_directory


def test_lwp_01_clean_codebase_passes() -> None:
    errors = check_directories(Path("."))
    assert errors == []


def test_lwp_01_rejects_forbidden_imports_and_eval() -> None:
    bad_code = """
import langchain
from vllm import LLM

def run_unsafe(user_input: str):
    return eval(user_input)
"""
    errors = check_source_code(bad_code, filename="bad_module.py", is_workflow=False)
    assert len(errors) == 3
    assert any("langchain" in e for e in errors)
    assert any("vllm" in e for e in errors)
    assert any("eval()" in e for e in errors)


def test_lwp_01_workflow_requires_stufe_and_begruendung() -> None:
    bad_workflow = """
async def handle(ctx, payload):
    return await ctx.detect_and_run_skill(text="Hallo", payload=payload)
"""
    err = check_source_code(bad_workflow, filename="workflows/bad_wf.py", is_workflow=True)
    assert len(err) == 1
    assert "begruendung" in err[0] and "stufe" in err[0]

    good_workflow = """
async def handle(ctx, payload):
    return await ctx.detect_and_run_skill(
        text="Hallo",
        payload=payload,
        stufe=1,
        begruendung="Vierstufiger Skill-Detektor und Ausführung",
    )
"""
    assert check_source_code(good_workflow, filename="workflows/good_wf.py", is_workflow=True) == []


def test_lwp_01_log_scanner_detects_iban_steuer_id_and_keys(tmp_path: Path) -> None:
    clean_line = '{"event": "skill_executed", "tenant_id": "stadt-musterhausen", "latency_ms": 14}'
    assert scan_jsonl_line(clean_line) == []

    iban_leak = '{"event": "step_done", "meta": "Konto DE89370400440532013000 geprüft"}'
    violations_iban = scan_jsonl_line(iban_leak)
    assert any("IBAN" in v for v in violations_iban)

    forbidden_key_leak = '{"event": "llm_call", "prompt": "Geheimer Bürgertext"}'
    violations_key = scan_jsonl_line(forbidden_key_leak)
    assert any("prompt" in v for v in violations_key)

    log_file = tmp_path / "app.jsonl"
    log_file.write_text(clean_line + "\n" + iban_leak + "\n", encoding="utf-8")
    dir_violations = scan_logs_directory(tmp_path)
    assert len(dir_violations) == 1
