import json
import re
import threading
import time
import random
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from models import Lead

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


_thread_local = threading.local()


def _get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = requests.Session()
    return _thread_local.session


def _get(url: str, session: requests.Session, delay: float = 2.0) -> Optional[BeautifulSoup]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        time.sleep(delay + random.uniform(0.5, 1.5))
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"    [WARN] {url} – {e}")
        return None


def _extract_next_data(soup: BeautifulSoup) -> Optional[dict]:
    """Liest __NEXT_DATA__ JSON aus Next.js-gerenderten Seiten."""
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None


def _city_from_location(text: str) -> str:
    """'Hamburg, Altona' → 'Hamburg'"""
    if not text:
        return ""
    return text.split(",")[0].strip()


# ─────────────────────────────────────────────────────────────
# KLEINANZEIGEN.DE
# ─────────────────────────────────────────────────────────────

def _parse_kleinanzeigen_detail(soup: BeautifulSoup) -> dict:
    """
    Extrahiert Firmennamen, Adresse und gewerblichen Status von einer Kleinanzeigen-Detailseite.
    Firmennamen stehen erst auf der Detailseite – nicht in der Übersicht.
    """
    result = {"company_name": None, "Adresse": None, "Stadt": None, "is_commercial": False}

    # Gewerblichkeitsstatus aus dem Kontaktbereich
    contact_el = soup.select_one("#viewad-contact")
    if contact_el:
        contact_text = contact_el.get_text()
        result["is_commercial"] = (
            "gewerblich" in contact_text.lower() or "Gewerblicher" in contact_text
        )

    # Firmenname: erster sinnvoller Text aus dem Seller-Block
    seller_divs = soup.select("[class*='seller']")
    if seller_divs:
        raw = seller_divs[0].get_text(separator="\n", strip=True)
        # Erste nicht-leere Zeile = Firmenname
        for line in raw.splitlines():
            line = line.strip()
            if line and len(line) > 2:
                result["company_name"] = line
                break

    # Adresse: PLZ Bundesland - Stadt (z.B. "39104 Sachsen-Anhalt - Magdeburg")
    locality_el = soup.select_one("#viewad-locality")
    if locality_el:
        raw_loc = locality_el.get_text(strip=True)
        result["Adresse"] = raw_loc
        # Stadt ist der letzte Teil nach " - "
        if " - " in raw_loc:
            result["Stadt"] = raw_loc.split(" - ")[-1].strip()
        else:
            result["Stadt"] = _city_from_location(raw_loc)

    return result


def _fetch_kleinanzeigen_detail(url: str) -> Optional[Lead]:
    """Thread-safe Wrapper: lädt eine Detailseite und gibt Lead zurück, oder None bei Privat."""
    soup = _get(url, _get_thread_session(), delay=2.5)
    if not soup:
        return None
    detail = _parse_kleinanzeigen_detail(soup)
    if not detail["is_commercial"]:
        return None
    return Lead(
        company_name=detail["company_name"],
        Adresse=detail["Adresse"],
        Stadt=detail["Stadt"],
        listing_url=url,
        source="kleinanzeigen",
    )


def scrape_kleinanzeigen(pages: int = 3, max_detail_fetches: int = 60) -> list[Lead]:
    """
    Neubauprojekte auf Kleinanzeigen.de.
    Übersichtsseiten → Listing-URLs sammeln → Detailseiten für Firmennamen abrufen.
    Nur gewerbliche Anbieter (= Bauträger) werden behalten.
    """
    session = requests.Session()
    listing_urls: list[str] = []

    # Phase 1: Übersichtsseiten – nur URLs sammeln
    for page in range(1, pages + 1):
        url = f"https://www.kleinanzeigen.de/s-immobilien-kaufen-neubau/seite:{page}/k0"
        print(f"  [Kleinanzeigen] Übersicht Seite {page}")
        soup = _get(url, session)
        if not soup:
            continue
        for art in soup.select("article.aditem"):
            href = art.get("data-href", "")
            if href:
                listing_urls.append("https://www.kleinanzeigen.de" + href)

    print(f"    → {len(listing_urls)} Listing-URLs gesammelt")

    # Phase 2: Detailseiten parallel (5 Threads) – Firmennamen + Adresse + Commercial-Flag
    leads: list[Lead] = []
    target_urls = listing_urls[:max_detail_fetches]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_kleinanzeigen_detail, url): url for url in target_urls}
        for future in as_completed(futures):
            result = future.result()
            if result:
                leads.append(result)

    print(f"    → {len(leads)} gewerbliche Leads ({len(target_urls)} Seiten gecheckt)")
    return leads


# ─────────────────────────────────────────────────────────────
# IMMOWELT.DE
# ─────────────────────────────────────────────────────────────

def scrape_immowelt(pages: int = 2) -> list[Lead]:
    """Neubau-Wohnungen auf Immowelt – versucht __NEXT_DATA__, fällt auf HTML zurück."""
    session = requests.Session()
    leads: list[Lead] = []

    for page in range(1, pages + 1):
        url = (
            "https://www.immowelt.de/suche/wohnungen/kaufen?typ=neubau"
            + (f"&cp={page}" if page > 1 else "")
        )
        print(f"  [Immowelt] Seite {page}")
        soup = _get(url, session, delay=3.0)
        if not soup:
            continue

        data = _extract_next_data(soup)
        if data:
            try:
                results = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("searchResult", {})
                    .get("results", [])
                )
                for item in results:
                    addr = item.get("locationAddress", {})
                    city = addr.get("city", "")
                    street = f"{addr.get('street', '')} {addr.get('houseNumber', '')}".strip()
                    adresse = f"{street}, {city}".strip(", ") if street else city
                    key = item.get("globalObjectKey", "")
                    leads.append(Lead(
                        company_name=item.get("contactName") or item.get("companyName") or None,
                        Stadt=city,
                        Adresse=adresse or None,
                        listing_url=f"https://www.immowelt.de/expose/{key}" if key else None,
                        source="immowelt",
                    ))
                print(f"    → {len(leads)} Leads (via __NEXT_DATA__)")
                continue
            except Exception as e:
                print(f"    [WARN] JSON-Parse fehlgeschlagen: {e} – HTML-Fallback")

        # HTML-Fallback
        for card in soup.select("[data-testid='serp-core-classified-card-testid']"):
            try:
                addr_el = card.select_one("[data-testid='classified-location']")
                link_el = card.select_one("a[href*='/expose/']")
                addr_text = addr_el.get_text(strip=True) if addr_el else ""
                leads.append(Lead(
                    Stadt=_city_from_location(addr_text),
                    Adresse=addr_text or None,
                    listing_url=(link_el["href"] if link_el["href"].startswith("http") else "https://www.immowelt.de" + link_el["href"]) if link_el else None,
                    source="immowelt",
                ))
            except Exception:
                continue

        print(f"    → {len(leads)} Leads bisher")

    return leads


# ─────────────────────────────────────────────────────────────
# IMMOSCOUT24.DE
# ─────────────────────────────────────────────────────────────


def scrape_immoscout(pages: int = 2) -> list[Lead]:
    """Neubau-Wohnungen auf ImmoScout24 – benötigt ggf. Proxy bei größerem Volumen."""
    session = requests.Session()
    leads: list[Lead] = []

    for page in range(1, pages + 1):
        url = (
            "https://www.immoscout24.de/Wohnung-Kaufen/Deutschland"
            + (f"?pagenumber={page}" if page > 1 else "")
        )
        print(f"  [ImmoScout24] Seite {page}")
        soup = _get(url, session, delay=4.0)
        if not soup:
            continue

        data = _extract_next_data(soup)
        if data:
            try:
                entries = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("searchResult", {})
                    .get("resultlistEntries", [{}])[0]
                    .get("resultlistEntry", [])
                )
                for entry in entries:
                    expose = entry.get("resultlistRealEstate", {})
                    addr = expose.get("address", {})
                    city = addr.get("city", "")
                    street = f"{addr.get('street', '')} {addr.get('houseNumber', '')}".strip()
                    adresse = f"{street}, {city}".strip(", ") if street else city
                    expose_id = expose.get("id", "")
                    leads.append(Lead(
                        company_name=expose.get("contactDetails", {}).get("officeName") or None,
                        Stadt=city,
                        Adresse=adresse or None,
                        listing_url=f"https://www.immoscout24.de/expose/{expose_id}" if expose_id else None,
                        source="immoscout24",
                    ))
                print(f"    → {len(leads)} Leads (via __NEXT_DATA__)")
                continue
            except Exception as e:
                print(f"    [WARN] JSON-Parse fehlgeschlagen: {e} – HTML-Fallback")

        # HTML-Fallback
        for item in soup.select("[data-testid='result-list-entry']"):
            try:
                addr_el = item.select_one("[data-testid='result-list-entry__address']")
                company_el = item.select_one("[data-testid='result-list-entry__realtor-name']")
                link_el = item.select_one("a[href*='/expose/']")
                addr_text = addr_el.get_text(strip=True) if addr_el else ""
                leads.append(Lead(
                    company_name=company_el.get_text(strip=True) if company_el else None,
                    Stadt=_city_from_location(addr_text),
                    Adresse=addr_text or None,
                    listing_url=("https://www.immoscout24.de" + link_el["href"]) if link_el else None,
                    source="immoscout24",
                ))
            except Exception:
                continue

        print(f"    → {len(leads)} Leads bisher")

    return leads
