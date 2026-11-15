"""Lokaler AES-256-GCM Zwei-Fassungen-Tresor (`RedactionVault`, LWP-03, Schutzregel F-01).
Erzeugt zu jedem Rohtext konsistent eine pseudonymisierte `redacted_view`
(z. B. `Max Mustermann` -> `<PERSON_1>`), sodass identische Entitäten in Antrag
und Wissens-Chunks denselben Platzhalter erhalten und Zitat-Prüfungen (`VAL-007`)
exakt auf der `redacted_view` funktionieren.
Die Zuordnungstabelle wird lokal mit AES-256-GCM verschlüsselt gehalten.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from plattform.policy_privacy.pii_detectors import detect_all_pii_spans


class RedactionVault:
    """Vorgangsgebundener AES-256-GCM Zwei-Fassungen-Tresor."""

    def __init__(
        self,
        master_key: bytes | None = None,
        known_persons: list[str] | None = None,
    ) -> None:
        if master_key is None:
            env_b64 = os.environ.get(
                "LOCAL_VAULT_MASTER_KEY_B64",
                "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
            )
            raw_key = base64.b64decode(env_b64)
            master_key = raw_key.ljust(32, b"0")[:32]
        if len(master_key) != 32:
            raise ValueError("AES-256-GCM Schlüssel muss exakt 32 Bytes lang sein.")
        self._aesgcm = AESGCM(master_key)
        self._known_persons: list[str] = list(known_persons or [])
        self._counters: dict[str, int] = {}
        self._value_to_token: dict[tuple[str, str], str] = {}
        self._encrypted_mapping_blob: bytes | None = None
        self._nonce: bytes | None = None

    def register_person_name(self, full_name: str) -> None:
        if full_name and full_name not in self._known_persons:
            self._known_persons.append(full_name)

    def _get_or_assign_token(self, entity_type: str, value: str) -> str:
        normalized = " ".join(value.strip().split())
        key = (entity_type, normalized)
        if key in self._value_to_token:
            return self._value_to_token[key]
        idx = self._counters.get(entity_type, 0) + 1
        self._counters[entity_type] = idx
        token = f"<{entity_type}_{idx}>"
        self._value_to_token[key] = token
        self._seal_mapping()
        return token

    def _seal_mapping(self) -> None:
        reverse_map = {token: val for (_, val), token in self._value_to_token.items()}
        plaintext = json.dumps(reverse_map, ensure_ascii=False).encode("utf-8")
        nonce = os.urandom(12)
        self._nonce = nonce
        self._encrypted_mapping_blob = self._aesgcm.encrypt(nonce, plaintext, None)

    def redact_text(self, text: str) -> str:
        """Erzeugt die konsistente `redacted_view` für einen Eingabetext oder RAG-Chunk."""
        spans = detect_all_pii_spans(text, extra_persons=self._known_persons)
        if not spans:
            return text

        out_parts: list[str] = []
        cursor = 0
        for sp in spans:
            out_parts.append(text[cursor : sp.start])
            token = self._get_or_assign_token(sp.entity_type, sp.text)
            out_parts.append(token)
            cursor = sp.end
        out_parts.append(text[cursor:])
        return "".join(out_parts)

    def rehydrate_text(self, redacted_text: str) -> str:
        """Entschlüsselt das Mapping aus dem AES-256-GCM-Tresor und stellt Klartextnamen wieder her."""
        if self._encrypted_mapping_blob is None or self._nonce is None:
            return redacted_text
        plaintext = self._aesgcm.decrypt(self._nonce, self._encrypted_mapping_blob, None)
        reverse_map: dict[str, str] = json.loads(plaintext.decode("utf-8"))
        result = redacted_text
        for token, cleartext in reverse_map.items():
            result = result.replace(token, cleartext)
        return result
