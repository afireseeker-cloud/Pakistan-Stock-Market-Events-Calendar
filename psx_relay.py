"""
dps.psx.com.pk returns 403 to GitHub Actions, so every PSX request in the
pipeline goes through the portfolio tracker's Cloudflare Worker (pkdata),
which forwards GET requests (company pages, PDFs) and relays the
/announcements and /payouts form posts with its own PSX session.

Links stored on events keep the real dps.psx.com.pk address; only the fetch
goes through the relay. Set PSX_BASE_URL=https://dps.psx.com.pk to fetch
directly from a machine PSX doesn't block.
"""
import os

PSX = "https://dps.psx.com.pk"
DEFAULT_RELAY = "https://pkdata.afireseeker.workers.dev"


def psx_base() -> str:
    return os.environ.get("PSX_BASE_URL", DEFAULT_RELAY).rstrip("/")


def via_relay(url: str) -> str:
    """A dps.psx.com.pk URL rewritten to go through the relay (others unchanged)."""
    return psx_base() + url[len(PSX):] if url.startswith(PSX) else url
