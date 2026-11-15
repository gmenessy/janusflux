"""Log-Klartext- und PII-Scanner (LWP-01):
Prüft alle *.jsonl-Dateien unter logs/ darauf, dass:
1. Keine verbotenen Klartext-Schlüssel (text, prompt, body, content, sachverhalt, raw_input, antrag_text, name, adresse, iban, steuer_id) vorkommen.
2. Keine deutschen/SEPA IBANs oder 11-stelligen Steuer-IDs im serialisierten Log-Eintrag stehen.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

FORBIDDEN_LOG_KEYS: set[str] = {
    "text",
    "prompt",
    "user_prompt",
    "system_prompt",
    "body",
    "content",
    "sachverhalt",
    "raw_input",
    "antrag_text",
    "dokument_text",
    "vorname",
    "nachname",
    "geburtsdatum",
    "iban",
    "steuer_id",
}

IBAN_PATTERN = re.compile(r"\bDE\d{2}\s?(?:\d{4}\s?){4}\d{2}\b", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _validate_iso7064_mod11_10(digits: str) -> bool:
    """Prüft die deutsche 11-stellige Steuer-ID nach ISO 7064 Mod 11, 10."""
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False
    product = 10
    for i in range(10):
        total = (int(digits[i]) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    check = (11 - product) % 10
    return check == int(digits[10])


STEUER_ID_CANDIDATE = re.compile(r"\b[1-9]\d{10}\b")


def _walk_keys(obj: Any) -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.append(str(k))
            found.extend(_walk_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_keys(item))
    return found


def scan_jsonl_line(line: str, source_name: str = "<line>", line_no: int = 1) -> list[str]:
    violations: list[str] = []
    stripped = line.strip()
    if not stripped:
        return violations

    try:
        record = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return [f"{source_name}:{line_no}: Ungültiges JSONL: {exc}"]

    for key in _walk_keys(record):
        if key.lower() in FORBIDDEN_LOG_KEYS:
            violations.append(
                f"{source_name}:{line_no}: Verbotener Klartext-Log-Key '{key}' gefunden."
            )

    if IBAN_PATTERN.search(stripped):
        violations.append(f"{source_name}:{line_no}: Klartext-IBAN im Log gefunden.")

    if EMAIL_PATTERN.search(stripped):
        violations.append(f"{source_name}:{line_no}: Klartext-E-Mail-Adresse im Log gefunden.")

    for match in STEUER_ID_CANDIDATE.findall(stripped):
        if _validate_iso7064_mod11_10(match):
            violations.append(f"{source_name}:{line_no}: Klartext-Steuer-ID im Log gefunden.")

    return violations


def scan_logs_directory(logs_dir: Path) -> list[str]:
    violations: list[str] = []
    if not logs_dir.exists():
        return violations
    for jsonl_path in sorted(logs_dir.rglob("*.jsonl")):
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            violations.extend(scan_jsonl_line(line, source_name=str(jsonl_path), line_no=idx))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    logs_dir = Path(args[0]) if args else Path("logs")
    violations = scan_logs_directory(logs_dir)
    if violations:
        for v in violations:
            print(f"[LOG-PII-LEAK] {v}", file=sys.stderr)
        return 1
    print("[LOG-SCAN-OK] 0 Klartext- oder PII-Einträge in Logs gefunden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
