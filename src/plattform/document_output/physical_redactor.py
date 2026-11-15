"""Physische PDF-Schwärzung mit 0-%-Resttext-Beweis über PyMuPDF (P11, LWP-09).
- Sucht sensible Strings oder nutzt übergebene Bounding-Boxes.
- Markiert alle Fundstellen über `page.add_redact_annot(rect, fill=(0, 0, 0))`
  und entfernt die Glyphen und Bildbereiche physisch aus dem PDF-Content-Stream mittels
  `page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)`.
- Prüft im Anschluss deterministisch den gesamten Text-Layer des Ausgabedokuments:
  Wenn auch nur ein einziger zu schwärzender String noch im Text-Layer steht, wird
  `RedactionResidualError` (Fail-Closed) ausgelöst.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz


class RedactionResidualError(RuntimeError):
    """Wird ausgelöst, wenn nach der physischen Schwärzung noch Resttext im PDF-Stream verbleibt."""


@dataclass(frozen=True)
class PhysicalRedactionResult:
    redacted_pdf_bytes: bytes
    removed_spans_count: int
    text_layer_clean: bool
    extracted_after_text: str


class PhysicalPdfRedactor:
    """Führt eine unumkehrbare physische Glyphen-Entfernung in PDF-Dokumenten durch."""

    def redact_sensitive_terms(
        self,
        pdf_bytes: bytes,
        sensitive_terms: list[str],
    ) -> PhysicalRedactionResult:
        terms = [t.strip() for t in sensitive_terms if t and t.strip()]
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        removed_count = 0

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            for term in terms:
                quads = page.search_for(term)
                for rect in quads:
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    removed_count += 1
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)

        out_bytes = doc.tobytes(garbage=4, deflate=True)
        doc.close()

        # Deterministischer 0-%-Resttext-Beweis auf dem neu geladenen PDF-Byte-Stream
        verify_doc = fitz.open(stream=out_bytes, filetype="pdf")
        remaining_text = "\n".join(
            verify_doc[idx].get_text("text") for idx in range(len(verify_doc))
        )
        verify_doc.close()

        leaked = [t for t in terms if t.lower() in remaining_text.lower()]
        if leaked:
            raise RedactionResidualError(
                f"Physische Schwärzung fehlgeschlagen: Resttext gefunden für {leaked}"
            )

        return PhysicalRedactionResult(
            redacted_pdf_bytes=out_bytes,
            removed_spans_count=removed_count,
            text_layer_clean=True,
            extracted_after_text=remaining_text,
        )
