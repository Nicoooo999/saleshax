# Saleshax Lead Engine – Bauträger Neubau

Scrapt Bauträger-Listings von Immoscout24, Kleinanzeigen und Immowelt, findet die Firmenwebsite, liest das Impressum und extrahiert per GPT-4o die Kontaktdaten. Output: eine kampagnenfähige CSV mit allen Variablen der E-Mail-Copy.

---

## Demo (kein Setup nötig)

`demo.html` im Browser öffnen — zeigt Workflow, Architektur, Bugfixes und eine interaktive Lead-Tabelle mit realen Ergebnissen aus einem echten Testlauf.

```bash
open demo.html   # macOS
# oder Datei im Browser per Doppelklick öffnen
```

**Empfohlener Tester-Pfad:** Demo zuerst → dann Setup unten für den echten Lauf.

---

## Ergebnis

`output/leads_saleshax.csv` — eine Zeile pro Lead, alle Copy-Variablen befüllt:

| Spalte | Quelle |
|--------|--------|
| `Anrede` | GPT-4o → Impressum |
| `firstName` | GPT-4o → Impressum |
| `lastName` | GPT-4o → Impressum |
| `email` | GPT-4o → Impressum |
| `phone` | GPT-4o → Impressum |
| `Adresse` | Listing-Seite |
| `Stadt` | Listing-Seite |
| `company_name` | Listing-Seite |
| `company_website` | DuckDuckGo-Suche |
| `listing_url` | Listing-Seite |
| `source` | kleinanzeigen / immowelt / immoscout24 |
| `enrichment_status` | enriched / no_email_found / no_website / … |

---

## Setup

```bash
cp .env.example .env
# API-Key eintragen (siehe unten)

pip install -r requirements.txt
python main.py
```

Schnelltest (Kleinanzeigen only, 20 Leads, ~5 Min.):
```bash
python main.py --quick
```

### API-Key

Für vollständige Kontaktextraktion (Anrede + Name) wird ein LLM-Key benötigt:

| Key | Wo holen | Qualität |
|-----|----------|----------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Empfohlen |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) | Alternative |

**Ohne Key:** Die Pipeline läuft trotzdem durch. E-Mails werden per Regex aus dem Impressum extrahiert, Namen aber nicht zuverlässig – die Leads sind dann nicht copy-ready.

---

## Architektur

```
Kleinanzeigen.de ──┐
Immowelt.de ───────┤──► scraper.py
Immoscout24.de ────┘        │
                            ▼
                     rohe Leads
                     (company_name, Stadt, Adresse, listing_url)
                            │
                            ▼
                      enricher.py
                            │
                     ┌──────┴──────┐
                     │             │
               DuckDuckGo    Impressum-Fetch
               (Website)     (/impressum, /kontakt)
                     │             │
                     └──────┬──────┘
                            │
                        GPT-4o
                  (Anrede, Name, E-Mail)
                            │
                            ▼
                 output/leads_saleshax.csv
```

**Warum Impressum?**  
§ 5 TMG verpflichtet jede deutsche Unternehmenswebsite zur Angabe von Verantwortlichem, E-Mail und Telefon. Das macht das Impressum zur zuverlässigsten öffentlichen Kontaktquelle — ohne Scraping-Walls oder Login.

---

## Beispiel-Output

```
Anrede  lastName     email                        Stadt       company_name
Herr    Maier        j.maier@bau-maier.de         München     Bau Maier GmbH
Frau    Schreiber    info@schreiber-immobilien.de  Hamburg    Schreiber Immobilien
Herr    Kowalski     k@tk-projektentwicklung.de   Berlin      TK Projektentwicklung
```

---

## Skalierung auf mehr Volumen

| Hebel | Maßnahme |
|-------|----------|
| Anti-Bot | Rotating Proxies (ScraperAPI, Brightdata) für Immoscout/Immowelt |
| Geschwindigkeit | `asyncio` + `httpx` für paralleles Enrichment |
| Mehr Quellen | `immonet.de`, `neubau-kompass.de`, `neubaukompass.de` in `scraper.py` ergänzen |
| Bessere Trefferquote | Playwright für JS-Heavy Portale wenn Requests blockiert werden |
| E-Mail-Validierung | `py3-validate-email` oder Hunter.io API zur Bounce-Reduktion |

---

## Hinweise

Scraping erfolgt ausschließlich auf öffentlich zugänglichen Seiten im Rahmen der normalen Nutzung.
Impressum-Daten sind nach § 5 TMG zur öffentlichen Bereitstellung verpflichtet.
Für den Kampagnenversand gilt DSGVO Art. 6 Abs. 1 lit. f (berechtigtes Interesse im B2B-Kontext).
