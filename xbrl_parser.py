"""
Parser pro české XBRL výkazy z justice.cz.
Podporuje formát MF ČR (rozvaha, VZaZ) i obecné XBRL.
"""
import re
from typing import List, Dict, Tuple
from lxml import etree

# Mapování českých XBRL elementů na čitelné názvy
# Pokrývá nejčastější elementy z MF ČR taxonomie
CZ_LABELS = {
    # === ROZVAHA - AKTIVA ===
    "AktivaVcelku": "AKTIVA CELKEM",
    "PohledavkyZaUpsanyZakladniKapital": "Pohledávky za upsaný základní kapitál",
    "DlouhodobaMajetek": "Dlouhodobý majetek",
    "DlouhodobaNehmotnaMajetek": "Dlouhodobý nehmotný majetek",
    "DlouhodbaTechnologickeZhodnoceni": "Ocenitelná práva / tech. zhodnocení",
    "DlouhodobaHmotnaMajetek": "Dlouhodobý hmotný majetek",
    "PozemkyAStavby": "Pozemky a stavby",
    "HmotneMoviteVeciASouboryMovitychVeci": "Hmotné movité věci",
    "DlouhodobaFinancniMajetek": "Dlouhodobý finanční majetek",
    "ObeznaMajetek": "Oběžná aktiva",
    "Zasoby": "Zásoby",
    "Pohledavky": "Pohledávky",
    "DlouhodobePohledavky": "Dlouhodobé pohledávky",
    "KratkodobePohledavky": "Krátkodobé pohledávky",
    "KratkodobyFinancniMajetek": "Krátkodobý finanční majetek",
    "PenezniProstredky": "Peněžní prostředky",
    "CasoveRozliseniAktiv": "Časové rozlišení aktiv",
    # === ROZVAHA - PASIVA ===
    "PasivaVcelku": "PASIVA CELKEM",
    "VlastniKapital": "Vlastní kapitál",
    "ZakladniKapital": "Základní kapitál",
    "AzioAOstatniFondyZeZisku": "Ážio a ostatní fondy ze zisku",
    "VysledekHospodารeniMinulychLet": "VH minulých let",
    "VysledekHospodарeniMinulychLet": "VH minulých let",
    "NerozdelenyvysledekHospodارeniMinulychLet": "Nerozdělený zisk/ztráta min. let",
    "VysledekHospodارeniPoBezdaneni": "VH po zdanění (běžné období)",
    "VysledekHospodаренiBeznehoObdobi": "VH běžného období",
    "CiziZdroje": "Cizí zdroje",
    "Rezervy": "Rezervy",
    "DlouhodbeDependence": "Dlouhodobé závazky",
    "DlouhodobeZavazky": "Dlouhodobé závazky",
    "KratkobobeZavazky": "Krátkodobé závazky",
    "KratkodbеZavazky": "Krátkodobé závazky",
    "KratkobyZavazky": "Krátkodobé závazky",
    "CasoveRozliseniPasiv": "Časové rozlišení pasiv",
    # === VZaZ ===
    "TrzbyZProdeje": "Tržby z prodeje výrobků a služeb",
    "TrzbyZaProdejZbozi": "Tržby za prodej zboží",
    "VykonováSpotřeba": "Výkonová spotřeba",
    "VykonovaSpot&#345;eba": "Výkonová spotřeba",
    "SpotrebaMaterialu": "Spotřeba materiálu a energie",
    "Sluzby": "Služby",
    "ZmenaStavuZasob": "Změna stavu zásob",
    "AktivaceNakladu": "Aktivace nákladů",
    "OsobniNaklady": "Osobní náklady",
    "MzdoveNaklady": "Mzdové náklady",
    "NakladyNaSocialniZabezpeceni": "Náklady na soc. zabezpečení",
    "UpravaHodnotVProvozniOblasti": "Úpravy hodnot v provozní oblasti",
    "Odpisy": "Odpisy",
    "OstatniProvozniVynosy": "Ostatní provozní výnosy",
    "OstatniProvozniNaklady": "Ostatní provozní náklady",
    "ProvozniVysledekHospodareni": "Provozní VH",
    "VynosyZDlouhodobyhoFinancnihoMajetku": "Výnosy z DFM",
    "NakladoveUroky": "Nákladové úroky",
    "VynosoveUroky": "Výnosové úroky",
    "OstatniFinancniVynosy": "Ostatní finanční výnosy",
    "OstatniFinancniNaklady": "Ostatní finanční náklady",
    "FinancniVysledekHospodareni": "Finanční VH",
    "VysledekHospodareniPredZdanenim": "VH před zdaněním",
    "DanZPrijmu": "Daň z příjmů",
    "VysledekHospodareniPoZdaneni": "VH po zdanění",
    "VysledekHospodareniZaUcetniObdobi": "VH za účetní období",
    "CistyObratZaUcetniObdobiNebo": "Čistý obrat",
}


def _clean_tag(tag: str) -> str:
    """Odstraní namespace z tagu."""
    return re.sub(r"\{[^}]+\}", "", tag)


def _normalize_label(raw: str) -> str:
    """Pokusí se přeložit XBRL element na česky čitelný název."""
    clean = _clean_tag(raw)
    # Přímá shoda
    if clean in CZ_LABELS:
        return CZ_LABELS[clean]
    # Částečná shoda (case-insensitive prefix)
    clean_lower = clean.lower()
    for key, val in CZ_LABELS.items():
        if key.lower() in clean_lower or clean_lower in key.lower():
            return val
    # Fallback - rozdělit camelCase na slova
    spaced = re.sub(r"([A-Z])", r" \1", clean).strip()
    return spaced


def parse_xbrl(xml_bytes: bytes) -> dict:
    """
    Parsuje XBRL XML a vrátí dict:
    {
      "meta": {period, entity, ...},
      "rozvaha": [{label, current, prior}],
      "vzaz": [{label, current, prior}],
      "raw": [{tag, value, context, unit}]
    }
    """
    # iXBRL detekce - pokud je to HTML s inline XBRL
    if b"ix:nonFraction" in xml_bytes or b"ix:nonNumeric" in xml_bytes:
        return parse_ixbrl(xml_bytes)

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return {"meta": {}, "rozvaha": [], "vzaz": [], "raw": []}

    # Namespace map
    nsmap = root.nsmap

    # Kontexty (období)
    contexts = {}
    for ctx in root.iter():
        if _clean_tag(ctx.tag) == "context":
            ctx_id = ctx.get("id", "")
            period = {}
            for child in ctx:
                ctag = _clean_tag(child.tag)
                if ctag == "period":
                    for p in child:
                        period[_clean_tag(p.tag)] = p.text
                elif ctag == "entity":
                    for e in child:
                        if _clean_tag(e.tag) == "identifier":
                            period["entity"] = e.text
            contexts[ctx_id] = period

    # Určit "current" a "prior" kontexty podle data
    def ctx_end_date(ctx_id: str) -> str:
        c = contexts.get(ctx_id, {})
        return c.get("endDate", c.get("instant", ""))

    # Všechny hodnoty
    raw_data = []
    for elem in root:
        tag = _clean_tag(elem.tag)
        if tag in ("context", "unit", "schemaRef"):
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        ctx_id = elem.get("contextRef", "")
        unit = elem.get("unitRef", "")
        decimals = elem.get("decimals", "")

        try:
            value = float(text.replace(",", "."))
        except ValueError:
            value = text

        raw_data.append({
            "tag": tag,
            "value": value,
            "context": ctx_id,
            "unit": unit,
            "decimals": decimals,
            "end_date": ctx_end_date(ctx_id),
        })

    # Najít dvě hlavní období (current = nejnovější, prior = předchozí)
    dates = sorted(set(d["end_date"] for d in raw_data if d["end_date"]), reverse=True)
    current_date = dates[0] if dates else ""
    prior_date = dates[1] if len(dates) > 1 else ""

    def get_values(tag_name: str) -> tuple:
        curr = next((d["value"] for d in raw_data if d["tag"] == tag_name and d["end_date"] == current_date), None)
        prior = next((d["value"] for d in raw_data if d["tag"] == tag_name and d["end_date"] == prior_date), None)
        return curr, prior

    # Sestavit rozvahu a VZaZ
    seen_tags = set()
    rozvaha_rows = []
    vzaz_rows = []

    ROZVAHA_KEYWORDS = ["aktiv", "pasiv", "kapital", "majetek", "zavazky", "pohledavky",
                        "zasoby", "rezervy", "penez", "financni"]
    VZAZ_KEYWORDS = ["trzby", "naklad", "vysledek", "hospodar", "dan", "odpis",
                     "mzd", "osobni", "provozni", "financni"]

    for item in raw_data:
        tag = item["tag"]
        if tag in seen_tags:
            continue
        if not isinstance(item["value"], float):
            continue

        seen_tags.add(tag)
        curr, prior = get_values(tag)
        label = _normalize_label(tag)
        row = {"label": label, "tag": tag, "current": curr, "prior": prior}

        tag_low = tag.lower()
        if any(k in tag_low for k in ROZVAHA_KEYWORDS):
            rozvaha_rows.append(row)
        elif any(k in tag_low for k in VZAZ_KEYWORDS):
            vzaz_rows.append(row)
        else:
            rozvaha_rows.append(row)  # zbytek do rozvahy

    # Meta informace
    meta = {}
    if contexts:
        first_ctx = next(iter(contexts.values()))
        meta["entity"] = first_ctx.get("entity", "")
    meta["period_current"] = current_date
    meta["period_prior"] = prior_date
    meta["total_items"] = len(raw_data)

    return {
        "meta": meta,
        "rozvaha": rozvaha_rows,
        "vzaz": vzaz_rows,
        "raw": raw_data,
    }


def parse_ixbrl(html_bytes: bytes) -> dict:
    """
    Parsuje Inline XBRL (iXBRL) pomocí regex - rychlejší než XML parser pro velké soubory.
    Hledá ix:nonFraction elementy a xbrli:context definice.
    """
    text = html_bytes.decode("utf-8", errors="replace")

    # Extrahovat kontexty: <xbrli:context id="...">...<xbrli:endDate>YYYY-MM-DD</xbrli:endDate>...
    contexts: Dict[str, str] = {}
    ctx_pattern = re.compile(
        r'<[^:]+:context[^>]+id=["\']([^"\']+)["\'][^>]*>.*?'
        r'(?:<[^:]+:(?:endDate|instant)>(\d{4}-\d{2}-\d{2})</[^:]+:(?:endDate|instant)>)',
        re.DOTALL
    )
    for m in ctx_pattern.finditer(text):
        contexts[m.group(1)] = m.group(2)

    raw_data = []

    # ix:nonFraction regex - zachytit atributy a obsah
    nf_pattern = re.compile(
        r'<[^:]+:nonFraction\s([^>]+)>(.*?)</[^:]+:nonFraction>',
        re.DOTALL
    )
    attr_name = re.compile(r'\bname=["\']([^"\']+)["\']')
    attr_ctx = re.compile(r'\bcontextRef=["\']([^"\']+)["\']')
    attr_scale = re.compile(r'\bscale=["\']([^"\']+)["\']')
    attr_sign = re.compile(r'\bsign=["\']([^"\']+)["\']')
    attr_unit = re.compile(r'\bunitRef=["\']([^"\']+)["\']')

    for m in nf_pattern.finditer(text):
        attrs = m.group(1)
        content = m.group(2).strip()

        name_m = attr_name.search(attrs)
        if not name_m:
            continue
        full_name = name_m.group(1)

        ctx_id = (attr_ctx.search(attrs) or type("", (), {"group": lambda s, i: ""})()).group(1)
        scale = (attr_scale.search(attrs) or type("", (), {"group": lambda s, i: "0"})()).group(1)
        sign = (attr_sign.search(attrs) or type("", (), {"group": lambda s, i: ""})()).group(1)
        unit = (attr_unit.search(attrs) or type("", (), {"group": lambda s, i: ""})()).group(1)

        # Vyčistit obsah (HTML entity, mezery, nebreakable space)
        val_str = re.sub(r'<[^>]+>', '', content)
        val_str = val_str.replace("\xa0", "").replace(" ", "").replace(" ", "").replace(" ", "")
        val_str = val_str.replace(",", ".").strip()

        try:
            value = float(val_str) * (10 ** int(scale))
            if sign == "-":
                value = -value
        except (ValueError, TypeError, OverflowError):
            continue

        item_tag = _clean_tag(full_name)
        end_date = contexts.get(ctx_id, "")
        raw_data.append({
            "tag": item_tag,
            "value": value,
            "context": ctx_id,
            "unit": unit,
            "end_date": end_date,
        })

    if not raw_data:
        return {"meta": {}, "rozvaha": [], "vzaz": [], "raw": []}

    dates = sorted(set(d["end_date"] for d in raw_data if d["end_date"]), reverse=True)
    current_date = dates[0] if dates else ""
    prior_date = dates[1] if len(dates) > 1 else ""

    def get_values(tag_name: str):
        curr = next((d["value"] for d in raw_data if d["tag"] == tag_name and d["end_date"] == current_date), None)
        prior = next((d["value"] for d in raw_data if d["tag"] == tag_name and d["end_date"] == prior_date), None)
        return curr, prior

    seen = set()
    rozvaha_rows = []
    vzaz_rows = []

    ROZVAHA_KEYWORDS = ["aktiv", "pasiv", "kapital", "majetek", "zavazky", "pohledavky",
                        "zasoby", "rezervy", "penez", "asset", "liabilit", "equity"]
    VZAZ_KEYWORDS = ["trzby", "naklad", "vysledek", "hospodar", "dan", "odpis",
                     "mzd", "osobni", "provozni", "revenue", "profit", "expense", "income"]

    for item in raw_data:
        tag = item["tag"]
        if tag in seen:
            continue
        seen.add(tag)
        curr, prior = get_values(tag)
        label = _normalize_label(tag)
        row = {"label": label, "tag": tag, "current": curr, "prior": prior}
        tag_low = tag.lower()
        if any(k in tag_low for k in VZAZ_KEYWORDS):
            vzaz_rows.append(row)
        else:
            rozvaha_rows.append(row)

    return {
        "meta": {
            "period_current": current_date,
            "period_prior": prior_date,
            "total_items": len(raw_data),
            "format": "ixbrl",
        },
        "rozvaha": rozvaha_rows,
        "vzaz": vzaz_rows,
        "raw": raw_data,
    }
