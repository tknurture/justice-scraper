# Justice Scraper API

REST API které stahuje účetní závěrky z [justice.cz](https://or.justice.cz) podle IČO firmy a vrací je jako strukturovaný XLSX soubor nebo PDF.

**Postaveno na:** Python · FastAPI · httpx · lxml · openpyxl

---

## Co API umí

- Vyhledá firmu na justice.cz podle IČO
- Stáhne nejnovější (nebo vybraný rok) účetní závěrku ze Sbírky listin
- Pokud je závěrka v **XBRL nebo iXBRL formátu** → vrátí **XLSX** s listy:
  - `Rozvaha` – aktiva, pasiva, vlastní kapitál (aktuální + předchozí rok)
  - `Výkaz zisku a ztráty` – výnosy, náklady, VH
  - `Všechna data` – kompletní raw XBRL hodnoty
  - `Info` – metadata závěrky
- Pokud má firma závěrku jen jako **PDF** → vrátí PDF přímo ze justice.cz
- Funguje pro všechny firmy v OR ČR (a.s., s.r.o., družstva…)

### XBRL vs PDF – která firma vrátí co?

| Typ firmy | Formát |
|---|---|
| Velké a.s., banky, pojišťovny, kótované firmy | ✅ XLSX (parsovaný XBRL/iXBRL) |
| Menší a.s. a s.r.o. | 📄 PDF ze Sbírky listin |
| Firmy bez závěrky v OR | ❌ 404 chyba |

---

## Endpointy

### `GET /vykazy/{ico}`
Stáhne nejnovější závěrku. Vrátí XLSX pokud je XBRL, jinak PDF.

```
GET /vykazy/45274649
→ XLSX (ČEZ má iXBRL)

GET /vykazy/27082440
→ PDF (Alza má jen PDF)
```

**Query parametry:**
- `rok` (int, volitelný) – konkrétní rok závěrky, výchozí = nejnovější

```
GET /vykazy/45274649?rok=2022
```

---

### `GET /vykazy/{ico}/json`
Stejná data jako XLSX, ale vrácená jako **JSON**. Funguje pouze pro firmy s XBRL závěrkou.

```json
{
  "ico": "45274649",
  "meta": {
    "period_current": "2024-12-31",
    "period_prior": "2023-12-31",
    "total_items": 488
  },
  "rozvaha": [
    {
      "label": "ifrs: Property Plant And Equipment",
      "tag": "ifrs:PropertyPlantAndEquipment",
      "current": 580704000000,
      "prior": 452132000000
    }
  ],
  "vzaz": [...]
}
```

---

### `GET /vykazy/{ico}/pdf`
Vynutí stažení PDF bez ohledu na dostupnost XBRL.

---

### `GET /info/{ico}`
Vrátí JSON seznam **všech dostupných závěrek** firmy (bez finančních dat). Vhodné pro zjištění, které roky jsou k dispozici a v jakém formátu.

```json
{
  "ico": "45274649",
  "subjekt_id": "59933",
  "count": 52,
  "zaverky": [
    {
      "year": 2024,
      "label": "účetní závěrka [2024]...",
      "detail_url": "https://or.justice.cz/...",
      "files": [
        { "url": "...", "name": "cez-2024.xhtml", "format": "ixbrl" }
      ]
    }
  ]
}
```

---

### `GET /health`
Health check.
```json
{ "status": "ok" }
```

---

## Spuštění lokálně

### Požadavky
- Python 3.9+

### Instalace

```bash
git clone https://github.com/tknurture/justice-scraper.git
cd justice-scraper

python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### Spuštění

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

API běží na `http://localhost:8000`  
Swagger dokumentace: `http://localhost:8000/docs`

---

## Spuštění přes Docker

```bash
docker-compose up
```

API běží na `http://localhost:8000`

---

## Použití v n8n

1. Přidej **HTTP Request** node
2. Method: `GET`
3. URL: `http://tvuj-server:8000/vykazy/{{ $json.ico }}`
4. Response Format: `File` (pro XLSX/PDF) nebo `JSON` (pro `/json` endpoint)

### Ošetření chyb v n8n
Firma nemusí mít závěrku na justice.cz (nová firma, s.r.o. bez povinnosti). API vrátí `404`. Doporučuji za HTTP node přidat podmínku:

```
IF {{ $node["JUSTICE"].json.error }} existuje → přeskoč / zaloguj
```

---

## Struktura projektu

```
justice-scraper/
├── main.py          # FastAPI aplikace, definice endpointů
├── scraper.py       # Scraping justice.cz (hledání firmy, seznam závěrek, stahování)
├── xbrl_parser.py   # Parser XBRL a iXBRL formátů
├── xlsx_export.py   # Generátor XLSX souboru (styly, listy)
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## Jak upravovat

### Přidat nový endpoint

V `main.py` přidej novou funkci s dekorátorem:

```python
@app.get("/muj-endpoint/{ico}")
def muj_endpoint(ico: str):
    _, zaverky = _resolve_company(ico)
    # tvoje logika
    return {"ico": ico, "data": ...}
```

### Přidat překlad XBRL elementu

V `xbrl_parser.py` je slovník `CZ_LABELS`. Přidej nový záznam:

```python
CZ_LABELS = {
    ...
    "NazevXbrlElementu": "Čitelný český název",
}
```

Název elementu najdeš v listu `Všechna data` ve sloupci `Tag`.

### Změnit vzhled XLSX

V `xlsx_export.py` jsou funkce `_header_style()` a `_subheader_style()`. Barvy jsou v hex formátu:

```python
def _header_style():
    return {
        "font": Font(bold=True, color="FFFFFF", size=11),
        "fill": PatternFill("solid", fgColor="1E3A5F"),  # ← změň barvu záhlaví
        ...
    }
```

### Přidat další filtr závěrek

Ve `scraper.py` funkce `list_zaverky()` hledá řádky obsahující `"účetní závěrka"`. Pokud chceš i výroční zprávy:

```python
if "účetní závěrka" not in row_text.lower() and "výroční zpráva" not in row_text.lower():
    continue
```

---

## Limity a omezení

- **Justice.cz nemá veřejné API** – scraping může selhat při změně struktury webu
- **Rychlost:** XBRL závěrky velkých firem jsou 10–50 MB, parsování trvá 30–90 sekund
- **Vercel Hobby** (60s timeout) nestačí pro velké iXBRL soubory – doporučuji VPS nebo Railway
- **Firmy bez závěrky:** API vrátí 404 – ošetři to v n8n podmínkou
- **PDF závěrky:** API je vrátí, ale neextrahuje z nich data (PDF parsing není implementován)
