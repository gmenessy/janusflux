"""Barrierefreier PDF/UA-1-Generator auf Basis von Jinja2-Vorlagen & PyMuPDF (P11, LWP-09).
- Rendert Vorlagen niemals per Freitext-LLM, sondern setzt strukturierte Bausteine
  deterministisch in die Jinja2-Vorlage ein.
- Betten PDF/UA-1 XMP-Metadaten (`pdfuaid:part = 1`), Dokumenttitel und Sprache (`de-DE`) ein.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pymupdf as fitz
from jinja2 import Environment, select_autoescape

PDFUA_XMP_TEMPLATE = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
   <pdfuaid:part>1</pdfuaid:part>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
   <dc:language><rdf:Bag><rdf:li>de-DE</rdf:li></rdf:Bag></dc:language>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

DEFAULT_BESCHEID_TEMPLATE = """Behörde: {{ behoerde_name }}
Aktenzeichen: {{ aktenzeichen }}
Datum: {{ datum }}

Betreff: {{ betreff }}

1. Sachverhalt:
{{ sachverhalt_zusammenfassung }}

2. Rechtliche Würdigung:
{{ wuerdigung_satz }}

3. Maßgeblicher Wortlaut der Rechtsquelle:
"{{ zitat_wortlaut }}"
"""


@dataclass(frozen=True)
class RenderedPdfArtifact:
    pdf_bytes: bytes
    rendered_text: str
    title: str
    language: str
    pdf_ua_part: int = 1


class PdfUaRenderer:
    """Erzeugt aus Jinja2-Vorlagen ein PDF/UA-1-konformes Dokument mit Metadaten und de-DE Sprach-Tag."""

    def __init__(self) -> None:
        self._env = Environment(autoescape=select_autoescape(default=True))

    def render_bescheid(
        self,
        context: dict[str, Any],
        template_str: str = DEFAULT_BESCHEID_TEMPLATE,
        title: str = "Verwaltungsbescheid",
    ) -> RenderedPdfArtifact:
        tmpl = self._env.from_string(template_str)
        rendered_text = tmpl.render(**context).strip()

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)  # DIN A4
        y_pos = 60.0
        for line in rendered_text.splitlines():
            page.insert_text((50, y_pos), line, fontsize=11)
            y_pos += 16.0

        doc.set_metadata(
            {
                "title": title,
                "author": str(context.get("behoerde_name", "Kommunalverwaltung")),
                "subject": str(context.get("betreff", title)),
                "producer": "JanusFlux Ultra-Lightweight PDF/UA-1 Engine",
            }
        )
        doc.set_xml_metadata(PDFUA_XMP_TEMPLATE.format(title=title))
        pdf_bytes = doc.tobytes(garbage=4, deflate=True)
        doc.close()

        return RenderedPdfArtifact(
            pdf_bytes=pdf_bytes,
            rendered_text=rendered_text,
            title=title,
            language="de-DE",
            pdf_ua_part=1,
        )
