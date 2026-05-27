"""
Scraper pro justice.cz - stahuje účetní závěrky z OR/Sbírky listin.
Používá persistent HTTP session pro zachování cookies při stahování.
"""
import io
import re
import zipfile
from typing import List, Tuple, Dict
import httpx
from bs4 import BeautifulSoup

JUSTICE_BASE = "https://or.justice.cz"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _make_client() -> httpx.Client:
    """Vytvoří HTTP klienta se session cookies a správnými hlavičkami."""
    client = httpx.Client(
        headers=HEADERS,
        follow_redirects=True,
        timeout=60,
    )
    # Navštívit hlavní stránku pro získání session cookie
    try:
        client.get(JUSTICE_BASE + "/ias/ui/rejstrik")
    except Exception:
        pass
    return client


class _JusticeSession:
    """Context manager pro httpx.Client s inicializací session cookie."""
    def __init__(self):
        self._client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=60)

    def __enter__(self):
        try:
            self._client.get(JUSTICE_BASE + "/ias/ui/rejstrik")
        except Exception:
            pass
        return self._client

    def __exit__(self, *_):
        self._client.close()


def get_subjekt_id(ico: str) -> str:
    """Najde subjektId firmy podle IČO na justice.cz."""
    with _JusticeSession() as client:
        url = f"{JUSTICE_BASE}/ias/ui/rejstrik-$firma?ico={ico.strip()}"
        r = client.get(url, timeout=30)
        r.raise_for_status()

        for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
            match = re.search(r"subjektId=(\d+)", a["href"])
            if match:
                return match.group(1)

        match = re.search(r"subjektId=(\d+)", r.text)
        if match:
            return match.group(1)

        raise ValueError(f"Firma s IČO {ico} nebyla nalezena na justice.cz")


def list_zaverky(subjekt_id: str) -> List[dict]:
    """
    Vrátí seznam účetních závěrek ze Sbírky listin.
    Každý záznam: {year, label, detail_url, doc_id}
    """
    url = f"{JUSTICE_BASE}/ias/ui/vypis-sl-firma?subjektId={subjekt_id}"
    with _JusticeSession() as client:
        r = client.get(url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

    results = []
    tables = soup.find_all("table")
    if not tables:
        return results

    doc_table = max(tables, key=lambda t: len(t.find_all("tr")))

    for row in doc_table.find_all("tr"):
        row_text = row.get_text(" ", strip=True)
        if "účetní závěrka" not in row_text.lower():
            continue

        links = row.find_all("a", href=True)
        if not links:
            continue

        href = links[0]["href"]
        full_url = href if href.startswith("http") else JUSTICE_BASE + "/ias/ui/" + href.lstrip("./")

        year_match_bracket = re.search(r"\[(\d{4})\]", row_text)
        year_match_plain = re.search(r"20\d{2}", row_text)
        if year_match_bracket:
            year = int(year_match_bracket.group(1))
        elif year_match_plain:
            year = int(year_match_plain.group())
        else:
            year = 0

        doc_match = re.search(r"dokument=(\d+)", href)
        doc_id = doc_match.group(1) if doc_match else ""

        results.append({
            "year": year,
            "label": row_text[:150],
            "detail_url": full_url,
            "doc_id": doc_id,
        })

    results.sort(key=lambda x: x["year"], reverse=True)
    return results


def get_download_links(detail_url: str, client: httpx.Client = None) -> List[dict]:
    """
    Otevře detail stránku závěrky a vrátí seznam stažitelných souborů.
    Každý: {url, name, format}
    """
    own_client = client is None
    if own_client:
        client = _JusticeSession().__enter__()

    try:
        r = client.get(detail_url, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
    finally:
        if own_client:
            client.close()

    files = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/ias/content/download" not in href and "download?id=" not in href:
            continue

        full_url = href if href.startswith("http") else JUSTICE_BASE + href
        link_text = a.get_text(" ", strip=True)

        text_low = link_text.lower() + href.lower()
        if ".zip" in text_low or "xbrl" in text_low:
            fmt = "xbrl"
        elif ".xml" in text_low:
            fmt = "xml"
        elif ".xhtml" in text_low:
            fmt = "ixbrl"
        else:
            fmt = "pdf"

        files.append({
            "url": full_url,
            "name": link_text[:100],
            "format": fmt,
        })

    return files


def _detect_format(data: bytes, content_type: str, hint: str) -> str:
    """
    Určí skutečný formát souboru podle content-type a magic bytes.
    Hint je formát odhadnutý z názvu souboru - použije se jako fallback.
    """
    ct = content_type.lower()

    # PDF magic bytes
    if data[:4] == b"%PDF":
        return "pdf"

    # ZIP (XBRL archiv)
    if data[:2] == b"PK":
        return "xbrl"

    # XML/XHTML - rozlišit iXBRL od čistého XBRL
    stripped = data.lstrip(b"\xef\xbb\xbf").strip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        if b"nonFraction" in data or b"nonNumeric" in data:
            return "ixbrl"
        return "xml"

    # Content-type fallback
    if "pdf" in ct:
        return "pdf"
    if "xhtml" in ct or "xml" in ct:
        return "ixbrl" if b"nonFraction" in data else "xml"

    return hint


def download_zaverka(zaverka: dict) -> Tuple[bytes, str, str]:
    """
    Kompletní flow: detail → soubory → stažení v rámci jedné session.
    Vrátí (data, content_type, format).
    """
    with _JusticeSession() as client:
        # 1. Načíst detail stránku (nutné pro session/cookies)
        r_detail = client.get(zaverka["detail_url"], timeout=30)
        r_detail.raise_for_status()

        soup = BeautifulSoup(r_detail.text, "lxml")
        files = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/ias/content/download" not in href and "download?id=" not in href:
                continue
            full_url = href if href.startswith("http") else JUSTICE_BASE + href
            link_text = a.get_text(" ", strip=True)
            text_low = link_text.lower() + href.lower()
            if ".zip" in text_low or "xbrl" in text_low:
                fmt = "xbrl"
            elif ".xml" in text_low:
                fmt = "xml"
            elif ".xhtml" in text_low:
                fmt = "ixbrl"
            else:
                fmt = "pdf"
            files.append({"url": full_url, "name": link_text[:100], "format": fmt})

        if not files:
            raise ValueError("Ke zvolené závěrce nebyly nalezeny žádné soubory.")

        # 2. Preferovat XBRL/iXBRL, fallback PDF
        xbrl = [f for f in files if f["format"] in ("xbrl", "xml", "ixbrl")]
        pdf = [f for f in files if f["format"] == "pdf"]
        chosen = (xbrl or pdf)[0]

        # 3. Stáhnout se stejnou session (obsahuje cookies z předchozích požadavků)
        r_file = client.get(chosen["url"], timeout=120)
        r_file.raise_for_status()
        data = r_file.content
        ct = r_file.headers.get("content-type", "")

        # Ověřit, že nejsme na HTML chybové stránce (justice.cz throttling)
        if "text/html" in ct and len(data) < 50_000:
            if xbrl and pdf:
                r_file = client.get(pdf[0]["url"], timeout=120)
                r_file.raise_for_status()
                data = r_file.content
                ct = r_file.headers.get("content-type", "")
                chosen = pdf[0]
            else:
                raise ValueError(
                    "Justice.cz odmítlo stažení souboru (pravděpodobně throttling). Zkus znovu."
                )

        # Určit skutečný formát podle content-type a obsahu - ne podle jména souboru
        actual_fmt = _detect_format(data, ct, chosen["format"])
        return data, ct, actual_fmt


def extract_xbrl_from_zip(data: bytes) -> List[Tuple[str, bytes]]:
    """
    Rozbalí ZIP a vrátí seznam (filename, obsah) pro XML/XBRL soubory.
    Pokud to není ZIP, zkusí zpracovat jako přímý XML/XHTML.
    """
    xmls = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".xml", ".xbrl", ".xhtml")):
                    xmls.append((name, zf.read(name)))
    except zipfile.BadZipFile:
        stripped = data.lstrip(b"\xef\xbb\xbf").strip()
        if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
            suffix = ".xhtml" if b"nonFraction" in data or b"<html" in data[:500].lower() else ".xml"
            xmls.append((f"document{suffix}", data))
    return xmls
