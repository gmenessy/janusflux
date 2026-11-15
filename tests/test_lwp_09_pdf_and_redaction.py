"""Abnahmetests für LWP-09: PDF/UA-1-Generator & Physische PDF-Schwärzung (P11)."""

import pymupdf as fitz

from plattform.document_output.pdf_ua_renderer import PdfUaRenderer
from plattform.document_output.physical_redactor import PhysicalPdfRedactor


def test_lwp_09_pdf_ua_renderer_embeds_xmp_and_renders_template() -> None:
    renderer = PdfUaRenderer()
    artifact = renderer.render_bescheid(
        {
            "behoerde_name": "Stadt Musterhausen",
            "aktenzeichen": "50-WG-2026/104",
            "datum": "15.11.2026",
            "betreff": "Anhörung / Nachforderung von Unterlagen",
            "sachverhalt_zusammenfassung": "Antragsteller Max Mustermann (IBAN DE89370400440532013000).",
            "wuerdigung_satz": "Die Frist endet am nächsten Werktag.",
            "zitat_wortlaut": "§ 31 Abs. 3 VwVfG",
        },
        title="Nachforderungsschreiben",
    )
    assert artifact.pdf_ua_part == 1
    assert artifact.language == "de-DE"

    doc = fitz.open(stream=artifact.pdf_bytes, filetype="pdf")
    xmp = doc.get_xml_metadata()
    assert "pdfuaid:part" in xmp
    assert "de-DE" in xmp
    doc.close()


def test_lwp_09_physical_redaction_removes_100_percent_of_sensitive_glyphs() -> None:
    renderer = PdfUaRenderer()
    initial_pdf = renderer.render_bescheid(
        {
            "behoerde_name": "Stadt Musterhausen",
            "aktenzeichen": "IFG-2026/99",
            "datum": "15.11.2026",
            "betreff": "Stellungnahme Ratsinformationssystem",
            "sachverhalt_zusammenfassung": (
                "Beschwerdeführer ist Herr Max Mustermann mit Konto DE89370400440532013000."
            ),
            "wuerdigung_satz": "Die Stellungnahme wird nach Schwärzung veröffentlicht.",
            "zitat_wortlaut": "§ 4 IFG NRW",
        }
    )

    redactor = PhysicalPdfRedactor()
    res = redactor.redact_sensitive_terms(
        initial_pdf.pdf_bytes,
        sensitive_terms=["Max Mustermann", "DE89370400440532013000"],
    )
    assert res.removed_spans_count >= 2
    assert res.text_layer_clean is True

    # Erneutes Auslesen über PyMuPDF darf 0 % der geschwärzten Zeichenketten enthalten
    check_doc = fitz.open(stream=res.redacted_pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in check_doc)
    check_doc.close()

    assert "Max Mustermann" not in full_text
    assert "DE89370400440532013000" not in full_text
    assert "Stellungnahme wird nach Schwärzung veröffentlicht" in full_text
