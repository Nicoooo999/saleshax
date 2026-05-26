import json
import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from typing import Optional
from dotenv import load_dotenv
from models import Lead

load_dotenv()

# Anthropic bevorzugen (via Claude Code Env), OpenAI als Fallback
try:
    import anthropic as _anthropic
    _ant_key = os.getenv("ANTHROPIC_API_KEY")
    if _ant_key:
        _llm_client = _anthropic.Anthropic(api_key=_ant_key)
        _LLM_PROVIDER = "anthropic"
    else:
        _llm_client = None
        _LLM_PROVIDER = "none"
except Exception:
    _llm_client = None
    _LLM_PROVIDER = "none"

if _LLM_PROVIDER == "none" and os.getenv("OPENAI_API_KEY"):
    from openai import OpenAI as _OpenAI
    _llm_client = _OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    _LLM_PROVIDER = "openai"

print(f"  LLM-Provider: {_LLM_PROVIDER}")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Portale und soziale Netzwerke als Website-Treffer ausschließen
_SKIP_DOMAINS = {
    "kleinanzeigen.de", "immoscout24.de", "immowelt.de", "immonet.de",
    "immobilienscout24.de", "facebook.com", "linkedin.com", "xing.com",
    "instagram.com", "youtube.com", "wikipedia.org", "google.com",
    "bing.com", "duckduckgo.com", "ebay.de", "trustpilot.com",
}

# Pfade, die ein deutsches Impressum typischerweise hat
_IMPRESSUM_PATHS = [
    "/impressum", "/impressum.html", "/impressum.php", "/impressum/",
    "/kontakt", "/kontakt.html", "/kontakt/",
    "/ueber-uns", "/ueber-uns.html", "/ueber-uns/",
    "/about", "/about-us", "/legal", "/legal-notice",
    "/datenschutz-impressum",
]


# ─────────────────────────────────────────────────────────────
# Schritt 1: Firmen-Website per DuckDuckGo finden
# ─────────────────────────────────────────────────────────────

def _company_name_slug(name: str) -> str:
    """Normiert Firmennamen für Domain-Vergleiche: 'HEYEN Immobilien GmbH' → 'heyen'"""
    stopwords = {"gmbh", "ag", "kg", "ug", "ohg", "gbr", "ev", "e.v.", "co", "und",
                 "immobilien", "consulting", "gmbh&co", "handelsvertretung"}
    name = name.lower()
    for ch in "äöüß":
        name = name.replace(ch, {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[ch])
    words = re.split(r"[\s\-&.,]+", name)
    meaningful = [w for w in words if w and w not in stopwords and len(w) > 2]
    return meaningful[0] if meaningful else name.split()[0] if name.split() else ""


def _domain_matches_company(domain: str, company_slug: str) -> bool:
    return company_slug and company_slug in domain.lower()


def _guess_domain(company_name: str) -> Optional[str]:
    """Konstruiert mögliche Domains aus dem Firmennamen und prüft sie direkt."""
    name = company_name.lower()
    for ch, repl in [("ä","ae"),("ö","oe"),("ü","ue"),("ß","ss")]:
        name = name.replace(ch, repl)
    name = re.sub(r"\b(gmbh|ag|kg|ug|gbr|e\.v\.|co\.?)\b", "", name, flags=re.I)
    name = re.sub(r"[^a-z0-9\s]", " ", name).strip()
    slug = re.sub(r"\s+", "-", name).strip("-")

    for candidate in [
        f"https://www.{slug}.de",
        f"https://{slug}.de",
        f"https://www.{slug.replace('-', '')}.de",
    ]:
        try:
            r = requests.get(candidate, headers=HEADERS, timeout=6, allow_redirects=True)
            if r.status_code == 200:
                return "/".join(candidate.split("/")[:3])
        except Exception:
            continue
    return None


def find_company_website(company_name: str, city: str) -> Optional[str]:
    """
    Sucht Firmenwebsite: DDGS → Domain-Guess-Fallback.
    Filtert Ergebnisse nach Namenübereinstimmung, um Fehlmatcher zu vermeiden.
    """
    from ddgs import DDGS

    slug = _company_name_slug(company_name)
    query = f"{company_name} {city} Impressum"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=10))

        # Priorität: 1) Domain enthält Firmennamen-Slug  2) .de-Domain  3) alles andere
        matched, de_domains, rest = [], [], []
        for r in results:
            href = r.get("href", "")
            if not href.startswith("http"):
                continue
            domain = re.sub(r"^https?://(www\.)?", "", href).split("/")[0].lower()
            if any(skip in domain for skip in _SKIP_DOMAINS):
                continue
            if _domain_matches_company(domain, slug):
                matched.append(href)
            elif domain.endswith(".de"):
                de_domains.append(href)
            else:
                rest.append(href)

        # Nur zurückgeben wenn guter Match – keine Wildcard-Fallbacks
        for href in (matched or de_domains)[:1]:
            parts = href.split("/")
            return "/".join(parts[:3])

    except Exception as e:
        print(f"    [WARN] DDGS-Suche für '{company_name}': {e}")

    # Fallback: Domain direkt aus Firmennamen konstruieren
    return _guess_domain(company_name)


# ─────────────────────────────────────────────────────────────
# Schritt 2: Impressum-Text laden
# ─────────────────────────────────────────────────────────────

_IMPRESSUM_KEYWORDS = {
    "impressum", "imprint", "legal", "rechtliches",
    "legal-notice", "legal-info", "datenschutz-impressum",
}


def _resolve_url(base_url: str, href: str) -> str:
    """Macht relative URLs absolut."""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base_url.rstrip("/") + href
    return base_url.rstrip("/") + "/" + href.lstrip("./")


def _soup_from_url(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "iframe"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _find_impressum_link(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Scannt alle Links auf der Homepage – findet Impressum-URL im Footer/Nav."""
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        link_text = a.get_text(strip=True).lower()
        href_lower = href.lower()
        if any(kw in href_lower or kw in link_text for kw in _IMPRESSUM_KEYWORDS):
            return _resolve_url(base_url, href)
    return None


def fetch_impressum(base_url: str) -> Optional[str]:
    """
    Phase 1: Homepage laden → Impressum-Link im Footer erkennen → direkt fetchen.
    Phase 2: Fallback auf Pfad-Liste (für Sites ohne Footer-Link).
    """
    # Phase 1: Homepage-Link-Detection
    homepage = _soup_from_url(base_url)
    if homepage:
        impressum_url = _find_impressum_link(homepage, base_url)
        if impressum_url and impressum_url.rstrip("/") != base_url.rstrip("/"):
            imp_soup = _soup_from_url(impressum_url)
            if imp_soup:
                text = _extract_text(imp_soup)
                if len(text) > 150:
                    return text[:4000]

    # Phase 2: Pfad-Fallback
    for path in _IMPRESSUM_PATHS:
        try:
            url = base_url.rstrip("/") + path
            resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "iframe"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if len(text) > 150:
                return text[:4000]
        except Exception:
            continue

    return None


# ─────────────────────────────────────────────────────────────
# Schritt 3a: Regex-Fallback für E-Mail + Name (kein API-Key nötig)
# ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Suche nach "Geschäftsführer: Vorname Nachname" und ähnlichen Mustern
_GF_RE = re.compile(
    # Titel ohne IGNORECASE – Namen müssen großgeschrieben sein
    r"(?:Geschäftsführer|Geschäftsführerin|Inhaber|Inhaberin|Verantwortlicher?|"
    r"Verantwortliche|Ansprechpartner|CEO|Managing Director|Eigentümer)"
    r"[\s:]+([A-ZÄÖÜ][a-zäöüß]{1,20}(?:[\s-][A-ZÄÖÜ][a-zäöüß]{1,20})+)",
)
# Kapitalisierte Nicht-Namen die im Impressum häufig vorkommen
_NON_NAMES = {
    "Umsatzsteuer","Registergericht","Handelsregister","Steuer","Gericht",
    "Bundesland","Deutschland","Ust","HRB","HRA","Amtsgericht","Finanzamt",
    "Identifikationsnummer","Steuernummer","Verantwortlich","Haftungshinweis",
    "Datenschutz","Impressum","Kontakt","Information","Gesellschaft","GmbH",
    "Inhalt","Angaben","Anschrift","Sitz","Recht","Pflicht",
}
_MALE_FIRST = {"Thomas","Michael","Andreas","Markus","Stefan","Christian","Martin",
               "Klaus","Jürgen","Peter","Hans","Wolfgang","Frank","Matthias","Daniel",
               "Alexander","Sebastian","Kai","Jan","David","Tobias","Florian","Marc",
               "Patrick","Maximilian","Felix","Leon","Lukas","Tim","Nico","Dennis",
               "Ralf","Robert","Christoph","Jochen","Günter","Karl","Bernd","Uwe",
               "Sven","Oliver","Thorsten","Dirk","Armin","Gerhard","Jens","Erik"}

def _extract_contact_regex(text: str) -> dict:
    """Schnelle Regex-Extraktion als Fallback wenn kein LLM-Key verfügbar."""
    result: dict = {}

    # E-Mail: bevorzuge keine generic-Adressen
    emails = _EMAIL_RE.findall(text)
    if emails:
        personal = [e for e in emails if not re.match(r"^(info|kontakt|mail|post|office|hallo|hello)@", e, re.I)]
        result["email"] = (personal or emails)[0]

    # Name aus Geschäftsführer-Muster
    for m in _GF_RE.finditer(text):
        full_name = m.group(1).strip()
        parts = full_name.split()
        if len(parts) < 2 or len(parts) > 4:
            continue
        # Keine Bindestriche in Nachnamen (schließt Compound-Wörter aus)
        # Keine zu langen Wörter (echte Nachnamen sind ≤ 15 Zeichen)
        # Kein Wort aus der Non-Names-Liste (auch Wortteile bei Bindestrichen prüfen)
        def _is_non_name(word: str) -> bool:
            return (word in _NON_NAMES
                    or any(p in _NON_NAMES for p in word.split("-"))
                    or len(word) > 20
                    or not word[0].isupper())
        if any(_is_non_name(p) for p in parts):
            continue
        result["firstName"] = parts[0]
        result["lastName"] = parts[-1]
        result["Anrede"] = "Herr" if parts[0] in _MALE_FIRST else "Frau"
        break

    return result


# ─────────────────────────────────────────────────────────────
# Schritt 3b: Kontaktdaten per LLM extrahieren (höhere Qualität)
# ─────────────────────────────────────────────────────────────

def extract_contact_llm(impressum_text: str, company_name: str) -> dict:
    """LLM extrahiert strukturierte Kontaktdaten aus dem Impressum-Rohtext."""
    if not _llm_client:
        print("    [WARN] Kein LLM-Provider – ANTHROPIC_API_KEY oder OPENAI_API_KEY in .env setzen")
        return {}

    prompt = f"""Du extrahierst Kontaktdaten aus dem Impressum einer deutschen Baufirma.

Firma: {company_name}

Impressum-Text:
{impressum_text}

Gib ausschließlich ein JSON-Objekt zurück mit diesen Feldern:
- "Anrede": "Herr" oder "Frau" – ableiten vom Vornamen der Kontaktperson; wenn unklar, "Herr"
- "firstName": Vorname der verantwortlichen Person (Geschäftsführer bevorzugen) – null wenn nicht vorhanden
- "lastName": Nachname dieser Person – null wenn nicht vorhanden
- "email": E-Mail-Adresse (persönliche Adresse bevorzugen gegenüber info@ oder kontakt@) – null wenn nicht vorhanden
- "phone": Telefonnummer – null wenn nicht vorhanden

Extrahiere nur reale Daten aus dem Text. Erfinde nichts."""

    try:
        if _LLM_PROVIDER == "anthropic":
            resp = _llm_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            return json.loads(raw)
        else:  # openai
            resp = _llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=400,
            )
            return json.loads(resp.choices[0].message.content)

    except Exception as e:
        print(f"    [WARN] LLM-Extraktion fehlgeschlagen: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# Haupt-Enrichment-Funktion
# ─────────────────────────────────────────────────────────────

def enrich_lead(lead: Lead) -> Lead:
    """
    Vollständiger Enrichment-Durchlauf für einen Lead:
    Firmennamen → Website → Impressum → Kontaktdaten
    """
    if not lead.company_name:
        lead.enrichment_status = "skipped_no_company"
        return lead

    print(f"  Anreichern: {lead.company_name} ({lead.Stadt or '–'})")

    # 1. Website suchen (überspringen wenn DDGS-Direktsuche bereits gesetzt hat)
    if lead.company_website:
        website = lead.company_website
    else:
        website = find_company_website(lead.company_name, lead.Stadt or "")
    if not website:
        print("    → Keine Website gefunden")
        lead.enrichment_status = "no_website"
        return lead

    lead.company_website = website
    time.sleep(1.0)

    # 2. Impressum laden
    impressum = fetch_impressum(website)
    if not impressum:
        print(f"    → Kein Impressum unter {website}")
        lead.enrichment_status = "no_impressum"
        return lead

    # 3. Kontakt extrahieren – LLM bevorzugt, Regex als Fallback
    if _llm_client:
        contact = extract_contact_llm(impressum, lead.company_name)
    else:
        contact = {}

    # Regex-Fallback für fehlende Felder
    if not contact.get("email") or not contact.get("lastName"):
        regex_data = _extract_contact_regex(impressum)
        for field in ("email", "firstName", "lastName", "Anrede"):
            if not contact.get(field) and regex_data.get(field):
                contact[field] = regex_data[field]

    lead.Anrede = contact.get("Anrede") or None
    lead.firstName = contact.get("firstName") or None
    lead.lastName = contact.get("lastName") or None
    lead.email = contact.get("email") or None
    if contact.get("phone") and not lead.phone:
        lead.phone = contact.get("phone")

    lead.enrichment_status = "enriched" if lead.email else "no_email_found"
    method = "LLM" if _llm_client else "Regex"
    print(f"    → [{method}] {lead.Anrede} {lead.lastName} <{lead.email or 'keine E-Mail'}>")

    return lead
