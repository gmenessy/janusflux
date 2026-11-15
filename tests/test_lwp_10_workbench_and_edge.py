"""Abnahmetests für LWP-10: Serverseitige BITV-2.0-Workbench (P8) & Lookahead-SSE-Edge-API (P1)."""

from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient

from plattform.edge.lookahead_sse import LookaheadSSEStreamGuard
from plattform.main import create_app


async def test_lwp_10_workbench_payload_hash_mismatch_returns_http_409() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post(
            "/api/v1/workbench/approvals",
            json={
                "tenant_id": "stadt-musterhausen",
                "run_id": "run-10",
                "step_id": "s3_human_approval",
                "level": "L1",
                "payload": {"betrag": "320,00 EUR", "empfaenger": "Person_1"},
            },
        )
        assert create_res.status_code == 200
        created = create_res.json()
        approval_id = created["approval_id"]
        original_hash = created["payload_hash"]

        # Prüfe BITV-2.0 Split-Screen HTML
        html_res = await client.get(f"/api/v1/workbench/approvals/{approval_id}/view")
        assert html_res.status_code == 200
        assert 'lang="de"' in html_res.text
        assert original_hash in html_res.text

        # Änderung eines einzigen Zeichens im freizugebenden Payload -> HTTP 409 PayloadHashMismatch
        bad_decide = await client.post(
            f"/api/v1/workbench/approvals/{approval_id}/decide",
            json={
                "decision": "APPROVED",
                "decided_by": "sachbearbeiter-1",
                "expected_payload_hash": original_hash,
                "payload_override": {"betrag": "321,00 EUR", "empfaenger": "Person_1"},
            },
        )
        assert bad_decide.status_code == 409
        assert "PayloadHashMismatch" in bad_decide.text

        # Exakt übereinstimmender Payload-Hash -> HTTP 200 APPROVED
        ok_decide = await client.post(
            f"/api/v1/workbench/approvals/{approval_id}/decide",
            json={
                "decision": "APPROVED",
                "decided_by": "sachbearbeiter-1",
                "expected_payload_hash": original_hash,
            },
        )
        assert ok_decide.status_code == 200
        assert ok_decide.json()["status"] == "APPROVED"


async def test_lwp_10_lookahead_sse_blocks_sentence_with_pii_before_emission() -> None:
    guard = LookaheadSSEStreamGuard()

    async def token_generator() -> AsyncIterator[str]:
        yield "Der Bescheid wurde "
        yield "erfolgreich vorbereitet. "
        yield "Die Auszahlung erfolgt auf Konto DE89 3704 "
        yield "0044 0532 0130 00 am Montag."

    emitted: list[str] = []
    async for event_str in guard.filter_token_stream(token_generator()):
        emitted.append(event_str)

    # Erster Satz ist sauber und wird ausgeliefert; zweiter Satz enthält IBAN und wird gestoppt!
    assert len(emitted) == 2
    assert "Der Bescheid wurde erfolgreich vorbereitet." in emitted[0]
    assert "event: security_halt" in emitted[1]
    assert "DE89" not in "".join(emitted)


async def test_lwp_10_api_skills_detect_and_dispatch() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        det_res = await client.post(
            "/api/v1/skills/detect",
            json={
                "tenant_id": "stadt-musterhausen",
                "schutzklasse": 2,
                "text": "Posteingang klassifizieren und an das zuständige Amt zuordnen.",
                "payload": {"eingang_text": "Antrag auf Wohngeld und Mietzuschuss."},
            },
        )
        assert det_res.status_code == 200
        assert det_res.json()["detected_skill_id"] == "eingang.routen"

        disp_res = await client.post(
            "/api/v1/skills/dispatch",
            json={
                "tenant_id": "stadt-musterhausen",
                "schutzklasse": 2,
                "text": "Posteingang klassifizieren",
                "payload": {"eingang_text": "Ich beantrage Wohngeld wegen Mieterhöhung."},
            },
        )
        assert disp_res.status_code == 200
        body = disp_res.json()
        assert body["execution"]["status"] == "COMPLETED"
        assert body["execution"]["output"]["ziel_organisationseinheit"] == "AMT_50_SOZIALES"
