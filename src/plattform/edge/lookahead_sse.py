"""Lookahead-SSE-Stream-Filter (P1, LWP-10).
Puffert ausgehende Token-Streams satzweise (`LookaheadBuffer`) und führt vor dem Senden
jedes Satzes an den Client einen deterministischen PII- und Prüfziffern-Scan (IBAN Mod 97,
Steuer-ID ISO 7064, E-Mail) durch.
Wird ein PII-Muster im Satz erkannt, wird der Satz NICHT ausgeliefert, sondern der Stream
sofort fail-closed mit einem Sicherheits-Event (`event: security_halt`) beendet.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator

from plattform.policy_privacy.pii_detectors import detect_all_pii_spans

SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n+")


class LookaheadSSEStreamGuard:
    """Satzweiser Lookahead-Puffer für Server-Sent Events mit deterministischer PII-Schranke."""

    def __init__(self, extra_persons: list[str] | None = None) -> None:
        self.extra_persons = list(extra_persons or [])

    def _contains_pii(self, sentence: str) -> str | None:
        spans = detect_all_pii_spans(sentence, extra_persons=self.extra_persons)
        if spans:
            return spans[0].entity_type
        return None

    async def filter_token_stream(
        self, token_chunks: AsyncIterable[str]
    ) -> AsyncIterator[str]:
        buffer = ""
        async for chunk in token_chunks:
            buffer += chunk
            while True:
                match = SENTENCE_BOUNDARY_RE.search(buffer)
                if not match:
                    break
                sentence = buffer[: match.end()]
                buffer = buffer[match.end() :]
                pii_type = self._contains_pii(sentence)
                if pii_type is not None:
                    yield (
                        f"event: security_halt\n"
                        f'data: {{"reason": "PII_LEAK_BLOCKED", "entity_type": "{pii_type}"}}\n\n'
                    )
                    return
                clean = sentence.strip()
                if clean:
                    yield f"event: message\ndata: {clean}\n\n"

        if buffer.strip():
            pii_type = self._contains_pii(buffer)
            if pii_type is not None:
                yield (
                    f"event: security_halt\n"
                    f'data: {{"reason": "PII_LEAK_BLOCKED", "entity_type": "{pii_type}"}}\n\n'
                )
                return
            yield f"event: message\ndata: {buffer.strip()}\n\n"
