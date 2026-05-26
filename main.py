"""
Saleshax Lead Engine – Bauträger Neubau
Scraping (Kleinanzeigen / Immowelt / ImmoScout24) + Enrichment (Impressum + GPT-4o)
→ kampagnenfähige CSV mit allen Copy-Variablen

Usage:
    python main.py              # Vollständige Pipeline (50 Leads)
    python main.py --quick      # Nur Kleinanzeigen, 20 Leads (schnellerer Test)
"""

import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from models import Lead
from scraper import scrape_kleinanzeigen, scrape_immowelt, scrape_immoscout
from enricher import enrich_lead

OUTPUT_DIR = Path("output")
OUTPUT_FILE = OUTPUT_DIR / "leads_saleshax.csv"

CSV_COLUMNS = [
    "Anrede", "firstName", "lastName", "email", "phone",
    "Adresse", "Stadt",
    "company_name", "company_website", "listing_url",
    "source", "enrichment_status",
]


# ─────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────

def deduplicate(leads: list[Lead]) -> list[Lead]:
    """Entfernt Duplikate anhand Firmennamen (case-insensitive) oder Listing-URL."""
    seen: set[str] = set()
    unique: list[Lead] = []
    for lead in leads:
        key = (lead.company_name or "").lower().strip() or lead.listing_url or ""
        if not key or key not in seen:
            if key:
                seen.add(key)
            unique.append(lead)
    return unique


def export_csv(leads: list[Lead]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for lead in leads:
            writer.writerow({col: getattr(lead, col, "") or "" for col in CSV_COLUMNS})
    print(f"\n  Gespeichert: {OUTPUT_FILE}")


def print_summary(leads: list[Lead]) -> None:
    total = len(leads)
    copy_ready = [l for l in leads if l.copy_ready()]
    enriched = sum(1 for l in leads if l.enrichment_status == "enriched")
    by_source = {}
    for l in leads:
        by_source[l.source or "unbekannt"] = by_source.get(l.source or "unbekannt", 0) + 1

    print("\n" + "=" * 55)
    print("  ERGEBNIS")
    print("=" * 55)
    print(f"  Leads gesamt:          {total}")
    print(f"  Angereichert:          {enriched}")
    print(f"  Copy-ready:            {len(copy_ready)}")
    print(f"  Output:                {OUTPUT_FILE}")
    print()
    for src, count in sorted(by_source.items()):
        print(f"  {src:<20} {count} Leads")
    print("=" * 55)

    if copy_ready:
        print("\n  Beispiel-Leads (copy-ready):")
        for lead in copy_ready[:5]:
            print(f"    • {lead.Anrede} {lead.lastName} <{lead.email}>")
            print(f"      {lead.company_name} | {lead.Stadt}")


# ─────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────

def run_pipeline(quick: bool = False) -> list[Lead]:
    pages = 1 if quick else 3
    target = 20 if quick else 50

    print("\n" + "=" * 55)
    print("  PHASE 1: SCRAPING")
    print("=" * 55)

    raw: list[Lead] = []

    print("\n[Kleinanzeigen.de]")
    raw += scrape_kleinanzeigen(pages=pages)

    if not quick:
        print("\n[Immowelt.de]")
        raw += scrape_immowelt(pages=2)

        print("\n[ImmoScout24.de]")
        raw += scrape_immoscout(pages=2)

    leads = deduplicate(raw)
    # Nur Leads mit Firmenname können sinnvoll angereichert werden
    enrichable = [l for l in leads if l.company_name]
    skipped = len(leads) - len(enrichable)

    print(f"\n  {len(raw)} Roh-Leads → {len(leads)} nach Dedup → {len(enrichable)} mit Firmenname")
    if skipped:
        print(f"  ({skipped} ohne Firmenname übersprungen)")

    # Cap auf Ziel-Volumen
    enrichable = enrichable[:target]

    print("\n" + "=" * 55)
    print("  PHASE 2: ENRICHMENT (Impressum + GPT-4o)")
    print("=" * 55)

    enriched: list[Lead] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(enrich_lead, lead): lead for lead in enrichable}
        done = 0
        for future in as_completed(futures):
            done += 1
            enriched.append(future.result())
            print(f"  [{done}/{len(enrichable)}] fertig")

    # Nicht-anreicherbare Leads ohne Firmenname anhängen (für Vollständigkeit)
    no_company = [l for l in leads if not l.company_name]
    all_leads = enriched + no_company

    return all_leads


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("  [HINWEIS] Kein API-Key gesetzt – Pipeline läuft im Regex-Modus.")
        print("  Für vollständige Kontaktextraktion (Anrede, Name) ANTHROPIC_API_KEY in .env setzen.\n")

    quick_mode = "--quick" in sys.argv
    if quick_mode:
        print("  Modus: --quick (Kleinanzeigen only, 20 Leads)")

    leads = run_pipeline(quick=quick_mode)
    export_csv(leads)
    print_summary(leads)
