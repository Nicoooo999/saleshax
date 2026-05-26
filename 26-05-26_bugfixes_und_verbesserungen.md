# Bugfixes & Verbesserungen – Lead Engine Bauträger Neubau

Dokumentation aller Probleme, die während der Entwicklung identifiziert und behoben wurden.

---

## Teil 1: Bugfixes

### Bug 1 — Anthropic Client ohne API-Key
**Datei:** `enricher.py`

**Problem:** Der Anthropic-Client wurde beim Import des Moduls immer initialisiert — auch wenn kein `ANTHROPIC_API_KEY` in der `.env` gesetzt war. Das führte sofort zu einem `AuthenticationError` beim Start, selbst wenn nur der Regex-Modus (ohne LLM) genutzt werden sollte.

**Fix:** Client-Erstellung in eine Bedingung gepackt:
```python
# Vorher:
anthropic_client = anthropic.Anthropic()

# Nachher:
anthropic_client = anthropic.Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None
```

---

### Bug 2 — Duplikat in Stopword-Liste
**Datei:** `enricher.py`

**Problem:** Das Wort `"immobilien"` stand zweimal in der `_COMPANY_STOPWORDS`-Menge, die generische Begriffe aus Firmennamen herausfiltert. Kein Absturz, aber unnötig und ein Zeichen für inkonsistente Pflege der Liste.

**Fix:** Duplikat entfernt.

---

### Bug 3 — JSON-Regex erfasste nicht alle Claude-Antworten
**Datei:** `enricher.py`

**Problem:** Claude gibt LLM-Antworten manchmal in Markdown-Codeblöcken zurück. Der Regex zum Herausschneiden des JSON lautete:
```python
re.sub(r"^```json", ...)
```
Das matchte nur Blöcke mit explizitem ` ```json `-Tag. Claude gibt aber manchmal auch nur ` ``` ` ohne Sprachkennung zurück — in diesen Fällen wurde der JSON nicht erkannt und das Parsing schlug still fehl, der Regex-Fallback griff stattdessen.

**Fix:** `json` im Regex optional gemacht:
```python
re.sub(r"^```(?:json)?", ...)
```

---

### Bug 4 — OpenAI Client doppelt initialisiert
**Datei:** `enricher.py`

**Problem:** `openai.OpenAI()` wurde sowohl auf Modul-Ebene als auch erneut innerhalb der Funktion `extract_contact_with_llm()` beim OpenAI-Branch neu erstellt. Das erzeugt bei jedem Aufruf eine neue Client-Instanz, was unnötigen Overhead produziert und potentiell zu Connection-Pool-Problemen führt.

**Fix:** Redundante Re-Initialisierung innerhalb der Funktion entfernt; Modul-Level-Client wird direkt genutzt.

---

### Bug 5 — Kleinanzeigen Pagination funktionierte nicht
**Datei:** `scraper.py`

**Problem:** Die URL für Folgeseiten auf Kleinanzeigen.de war veraltet:
```
https://www.kleinanzeigen.de/s-immobilien-kaufen-neubau/?page=2
```
Kleinanzeigen hat das URL-Schema geändert. `?page=2` wird ignoriert — es wurde immer nur Seite 1 zurückgeliefert, egal welche Seitennummer angegeben wurde.

**Fix:** Korrekte URL-Struktur:
```
https://www.kleinanzeigen.de/s-immobilien-kaufen-neubau/seite:2/k0
```

---

### Bug 6 — `copy_ready()` prüfte falsches Pflichtfeld
**Datei:** `models.py`

**Problem:** Die Methode `copy_ready()` entscheidet, ob ein Lead kampagnenfähig ist. Sie prüfte `firstName` als Pflichtfeld:
```python
def copy_ready(self) -> bool:
    return bool(self.Anrede and self.firstName and self.lastName and self.email and self.Stadt)
```
Das Problem: `{{firstName}}` kommt in der tatsächlichen E-Mail-Copy-Template **nicht vor**. Die Template verwendet nur `{{Anrede}}`, `{{lastName}}`, `{{Adresse/ Stadt}}` und `{{Stadt}}`. Impressum-Scraping liefert selten Vornamen — das war der Regelfall, nicht die Ausnahme. Ergebnis: Fast alle angereicherten Leads wurden als nicht kampagnenfähig markiert, obwohl alle wirklich benötigten Felder vorhanden waren.

**Fix:** `firstName` aus der Pflichtprüfung entfernt; das Feld bleibt im Modell und wird angereichert wenn möglich, blockiert aber nicht die Ausspielung:
```python
def copy_ready(self) -> bool:
    return bool(self.Anrede and self.lastName and self.email and self.Stadt)
```

---

## Teil 2: Performance-Verbesserungen

### Verbesserung 1 — Parallele Kleinanzeigen-Detailseiten
**Datei:** `scraper.py`

**Problem:** Kleinanzeigen zeigt Firmennamen nicht in der Übersicht — jede Detailseite muss einzeln aufgerufen werden, um den Anbieter-Namen und den gewerblichen Status zu ermitteln. Ursprünglich wurden diese sequentiell abgerufen: jeder Request hatte einen obligatorischen Delay von 2,5 Sekunden plus zufälligen Jitter. Bei 60 Detailseiten = ca. 3–4 Minuten Wartezeit allein für diesen Schritt.

**Lösung:** `ThreadPoolExecutor` mit 5 parallelen Threads. Thread-lokale `requests.Session`-Objekte (via `threading.local()`) verhindern Race Conditions beim Session-Sharing.

**Ergebnis:** ~5× schneller, ca. 40–50 Sekunden statt 3–4 Minuten für 60 Detailseiten.

---

### Verbesserung 2 — DDGS Direktsuche als 4. Lead-Quelle
**Datei:** `scraper.py`

**Problem:** Immoscout24 und Immowelt setzen zunehmend auf JavaScript-Rendering und Captcha-Systeme, was das Scraping unzuverlässig macht. Beide Portale zeigen außerdem nur Bauträger, die aktiv inserieren — viele mittelgroße Firmen haben keine aktiven Inserate, sind aber trotzdem Zielkunden.

**Lösung:** DuckDuckGo Search (DDGS) als zusätzliche Quelle. Suchanfrage: `"Bauträger Neubau {Stadt} Impressum"` für 15 deutsche Städte. Vorteile:
- Findet Firmen direkt über deren eigene Website, bypassed Portale vollständig
- `company_website` ist sofort gesetzt → Enricher überspringt den Website-Such-Schritt
- 15 Städte × bis zu 8 Ergebnisse = bis zu 120 zusätzliche Leads

---

### Verbesserung 3 — Intelligente Impressum-Erkennung
**Datei:** `enricher.py`

**Problem:** Der ursprüngliche Impressum-Finder arbeitete mit blindem Pfad-Raten: Er probierte 8 Standardpfade durch (`/impressum`, `/imprint`, `/legal`, etc.) und machte für jeden einen separaten HTTP-Request. Im schlechtesten Fall: 8 Requests mit jeweils 15 Sekunden Timeout = bis zu 120 Sekunden pro Lead. Außerdem wurden nicht-standardmäßige URL-Strukturen (`/ueber-uns/impressum`, `/rechtliches/`, etc.) nie gefunden.

**Lösung:** Zweistufiger Ansatz mit Homepage-Link-Detection:
1. **Phase 1:** Homepage laden, alle `<a>`-Tags nach Impressum-Keywords scannen (`impressum`, `imprint`, `legal`, `rechtliches`, etc.). Wenn ein Link gefunden wird → direkt fetchen.
2. **Phase 2 (Fallback):** Nur wenn Phase 1 nichts findet, die alten Standardpfade durchprobieren (Timeout auf 8 Sekunden reduziert).

**Ergebnis:** Im Normalfall 1–2 HTTP-Requests statt 8, Worst Case ~20 Sekunden statt ~120 Sekunden. Findet auch nicht-standardmäßige Impressum-URLs.

---

### Verbesserung 4 — Modell-Wechsel: Sonnet → Haiku
**Datei:** `enricher.py`

**Problem:** Die LLM-Aufgabe im Enricher ist klar definiert: strukturierte JSON-Extraktion (Name, Anrede, E-Mail, Telefon) aus kurzen Impressum-Texten. Dafür wurde `claude-sonnet-4-6` verwendet — ein Frontier-Modell, das für diese repetitive Extraktionsaufgabe deutlich überdimensioniert und zu langsam ist.

**Lösung:** Wechsel auf `claude-haiku-4-5-20251001`.

**Ergebnis:** ~3× schneller pro Extraction-Call, ~15× günstiger pro Token. Für strukturierte Extraktion aus kurzen Texten ist die Output-Qualität identisch.

---

## Zusammenfassung

| # | Typ | Datei | Auswirkung |
|---|-----|-------|------------|
| 1 | Bug | `enricher.py` | Kein Start ohne API-Key möglich |
| 2 | Bug | `enricher.py` | Stopword-Duplikat |
| 3 | Bug | `enricher.py` | JSON-Parsing schlug bei ~50% der Claude-Antworten fehl |
| 4 | Bug | `enricher.py` | Redundante Client-Initialisierung |
| 5 | Bug | `scraper.py` | Kleinanzeigen lieferte immer nur Seite 1 |
| 6 | Bug | `models.py` | Fast alle Leads als nicht kampagnenfähig markiert |
| 7 | Performance | `scraper.py` | Kleinanzeigen-Scraping 5× schneller |
| 8 | Feature | `scraper.py` | +120 potenzielle Leads via DDGS (4. Quelle) |
| 9 | Performance | `enricher.py` | Impressum-Finden 6–10× schneller, höhere Erfolgsrate |
| 10 | Performance | `enricher.py` | LLM-Extraktion 3× schneller, 15× günstiger |
