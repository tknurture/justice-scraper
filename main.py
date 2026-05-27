"""
FastAPI aplikace - Justice.cz Účetní Závěrky API

Endpointy:
  GET /info/{ico}           - metadata o dostupných závěrkách (JSON)
  GET /vykazy/{ico}         - nejnovější závěrka jako XLSX (pokud XBRL/iXBRL) nebo PDF
  GET /vykazy/{ico}/json    - nejnovější závěrka jako JSON (jen pro XBRL/iXBRL)
  GET /vykazy/{ico}/pdf     - stáhne PDF závěrku přímo z justice.cz

Spuštění: uvicorn main:app --host 0.0.0.0 --port 8000
"""
import io
from typing import List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from scraper import (
    get_subjekt_id, list_zaverky, get_download_links,
    download_zaverka, extract_xbrl_from_zip
)
from xbrl_parser import parse_xbrl
from xlsx_export import build_xlsx

app = FastAPI(
    title="Justice.cz Účetní Závěrky API",
    description=(
        "Stahuje účetní závěrky z justice.cz podle IČO.\n\n"
        "- **XBRL / iXBRL závěrky** → vrací strukturovaný XLSX s Rozvahou a VZaZ\n"
        "- **PDF závěrky** → vrací PDF přímo z justice.cz\n\n"
        "Navrženo pro použití v n8n přes HTTP node."
    ),
    version="1.0.0",
)


def _resolve_company(ico: str) -> Tuple[str, List[dict]]:
    """Najde firmu a vrátí (subjekt_id, seznam_závěrek)."""
    try:
        subjekt_id = get_subjekt_id(ico)
    except ValueError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"Chyba při hledání firmy na justice.cz: {e}")

    try:
        zaverky = list_zaverky(subjekt_id)
    except Exception as e:
        raise HTTPException(502, detail=f"Chyba při načítání Sbírky listin: {e}")

    if not zaverky:
        raise HTTPException(404, detail=f"Pro IČO {ico} nebyly nalezeny žádné účetní závěrky.")

    return subjekt_id, zaverky


def _pick_zaverka(zaverky: List[dict], rok: Optional[int]) -> dict:
    if rok:
        hits = [z for z in zaverky if z["year"] == rok]
        if not hits:
            raise HTTPException(404, detail=f"Závěrka za rok {rok} nebyla nalezena.")
        return hits[0]
    return zaverky[0]


@app.get("/info/{ico}", summary="Metadata o dostupných závěrkách")
def get_info(ico: str):
    """
    Vrátí JSON se seznamem všech účetních závěrek firmy z justice.cz.
    Neobsahuje finanční data – pouze metadata (rok, typ, URL detailu).
    """
    subjekt_id, zaverky = _resolve_company(ico)
    enriched = []
    for z in zaverky:
        try:
            files = get_download_links(z["detail_url"])
        except Exception:
            files = []
        enriched.append({**z, "files": files})

    return {
        "ico": ico,
        "subjekt_id": subjekt_id,
        "count": len(zaverky),
        "zaverky": enriched,
    }


@app.get(
    "/vykazy/{ico}",
    summary="Nejnovější závěrka jako XLSX (XBRL/iXBRL) nebo PDF",
)
def get_vykazy(
    ico: str,
    rok: Optional[int] = Query(None, description="Rok závěrky, výchozí = nejnovější"),
):
    """
    Stáhne závěrku a vrátí:
    - **XLSX** pokud je dostupná v XBRL nebo iXBRL formátu (listy: Rozvaha, VZaZ, Všechna data)
    - **PDF** pokud je dostupná jen PDF verze

    Ideální pro n8n HTTP node s `Response Format = File`.
    """
    _, zaverky = _resolve_company(ico)
    zaverka = _pick_zaverka(zaverky, rok)

    try:
        data, ct, fmt = download_zaverka(zaverka)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=f"Chyba při stahování závěrky: {e}")

    # XBRL nebo iXBRL → XLSX
    if fmt in ("xbrl", "xml", "ixbrl"):
        xmls = extract_xbrl_from_zip(data)
        if xmls:
            xmls.sort(key=lambda x: len(x[1]), reverse=True)
            _, xml_bytes = xmls[0]
            parsed = parse_xbrl(xml_bytes)
            if parsed.get("rozvaha") or parsed.get("vzaz"):
                xlsx_bytes = build_xlsx(parsed, ico, zaverka)
                filename = f"zaverka_{ico}_{zaverka['year']}.xlsx"
                return StreamingResponse(
                    io.BytesIO(xlsx_bytes),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )

    # PDF → ověřit magic bytes před odesláním
    if data[:4] != b"%PDF":
        raise HTTPException(
            502,
            detail=f"Justice.cz nevrátilo platný PDF soubor (obdrženo {len(data)} bytes, "
                   f"content-type: {ct}). Zkus znovu."
        )

    filename = f"zaverka_{ico}_{zaverka['year']}.pdf"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Format": fmt,
        },
    )


@app.get(
    "/vykazy/{ico}/json",
    summary="Nejnovější závěrka jako JSON (jen XBRL/iXBRL)",
)
def get_vykazy_json(
    ico: str,
    rok: Optional[int] = Query(None, description="Rok závěrky, výchozí = nejnovější"),
):
    """
    Vrátí finanční data jako JSON – vhodné pro přímé zpracování v n8n bez parsování souboru.
    Funguje pouze pokud je závěrka v XBRL nebo iXBRL formátu.
    """
    _, zaverky = _resolve_company(ico)
    zaverka = _pick_zaverka(zaverky, rok)

    try:
        data, ct, fmt = download_zaverka(zaverka)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        raise HTTPException(502, detail=str(e))

    if fmt not in ("xbrl", "xml", "ixbrl"):
        raise HTTPException(
            422,
            detail="Závěrka není v XBRL/iXBRL formátu. Použij /vykazy/{ico} pro stažení PDF."
        )

    xmls = extract_xbrl_from_zip(data)
    if not xmls:
        raise HTTPException(422, detail="Nepodařilo se extrahovat XML z dokumentu.")

    xmls.sort(key=lambda x: len(x[1]), reverse=True)
    _, xml_bytes = xmls[0]
    parsed = parse_xbrl(xml_bytes)

    return {
        "ico": ico,
        "zaverka": zaverka,
        "meta": parsed["meta"],
        "rozvaha": parsed["rozvaha"],
        "vzaz": parsed["vzaz"],
    }


@app.get(
    "/vykazy/{ico}/pdf",
    summary="Stáhne PDF závěrku přímo",
)
def get_vykazy_pdf(
    ico: str,
    rok: Optional[int] = Query(None, description="Rok závěrky, výchozí = nejnovější"),
):
    """Stáhne PDF verzi závěrky bez ohledu na dostupnost XBRL."""
    _, zaverky = _resolve_company(ico)
    zaverka = _pick_zaverka(zaverky, rok)

    try:
        data, ct, fmt = download_zaverka(zaverka)
    except Exception as e:
        raise HTTPException(502, detail=str(e))

    # Pokud dostaneme XBRL, stáhneme PDF přes jiný endpoint
    if fmt not in ("pdf",) or "pdf" not in ct:
        # Znovu stáhnout ale vynutit PDF
        try:
            files = get_download_links(zaverka["detail_url"])
            pdf_files = [f for f in files if f["format"] == "pdf"]
            if not pdf_files:
                raise HTTPException(404, detail="PDF závěrka není k dispozici.")
            from scraper import _make_client
            with _make_client() as client:
                client.get(zaverka["detail_url"])
                r = client.get(pdf_files[0]["url"], timeout=120)
                r.raise_for_status()
                data = r.content
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, detail=str(e))

    filename = f"zaverka_{ico}_{zaverka['year']}.pdf"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
def health():
    return {"status": "ok"}
