from pydantic import BaseModel
from typing import Optional


class Lead(BaseModel):
    # Copy-Variablen (alle müssen befüllt sein für Kampagnen-Ausspielung)
    Anrede: Optional[str] = None       # Herr / Frau
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    Adresse: Optional[str] = None      # Straße + Hausnummer + Stadt
    Stadt: Optional[str] = None        # nur die Stadt

    # Kontextdaten
    company_name: Optional[str] = None
    phone: Optional[str] = None
    listing_url: Optional[str] = None
    company_website: Optional[str] = None
    source: Optional[str] = None       # kleinanzeigen | immowelt | immoscout24
    enrichment_status: str = "pending"

    def copy_ready(self) -> bool:
        """True wenn alle Pflichtfelder für die E-Mail-Copy vorhanden sind."""
        return bool(self.Anrede and self.lastName and self.email and self.Stadt)
