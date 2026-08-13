"""
Parses PSX Notices (type=E) HTML responses -- a DIFFERENT table schema from
Companies Announcements (type=C, handled by parse_announcements.py).

Confirmed via live capture (11-Aug-2026): only 4 columns, not 6:
    <th>DATE</th><th>TIME</th><th>TITLE</th><th> </th> (attachment cell)

No SYMBOL or NAME columns -- PSX Notices are exchange-level (holiday
calendars, circuit breaker rules, risk warnings, listing/IPO notices),
not tied to one company, so there's nothing to key a symbol off of. This
is why parse_announcements.py (which requires >=6 cells) silently dropped
every row when pointed at type=E: it's not a broken feed, it's a narrower
one. scope is always "market" here, never "company".

This is also the feed IPO/book-building notices flow through, since
IPO-stage companies don't have a PSX symbol yet and so can't appear in the
symbol-keyed Companies Announcements feed at all.
"""
import json
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup

BASE_URL = "https://dps.psx.com.pk"
IMAGE_BASE_URL = "https://dps.psx.com.pk/download/image/"

CLASSIFICATION_RULES = [
    ("ipo_subscription", re.compile(
        r"public subscription|subscription.{0,20}(open|close)|initial public offering|"
        r"\bipo\b", re.I)),
    ("ipo_book_building", re.compile(r"book building", re.I)),
    ("ipo_prospectus", re.compile(r"prospectus", re.I)),
    # these three must come BEFORE ipo_listing -- "delisting" contains the
    # substring "listing", and sukuk/fund listing notices also say
    # "listing of X" in their title, so without checking these first every
    # one of them gets misclassified as a new-company equity IPO. Real
    # data confirmed this: 18 "ipo_listing" matches included 4 delistings,
    # 6 GoP sukuk notices, and 2 mutual fund listings -- only ~3 were
    # actual equity IPOs.
    ("delisting", re.compile(r"\bde-?listing\b", re.I)),
    ("debt_instrument_listing", re.compile(
        r"\bsukuk\b|\bijarah\b|\bGIS\b|\bGHS\b|government of pakistan", re.I)),
    ("fund_listing", re.compile(r"mutual fund|open-end|open end", re.I)),
    ("ipo_listing", re.compile(r"listing of|formally listed|gong ceremony", re.I)),
    ("market_holiday", re.compile(r"holiday calendar", re.I)),
    ("trading_halt", re.compile(r"circuit breaker|trading halt|suspension of trading", re.I)),
    ("risk_warning", re.compile(r"risk warning|RWA\b", re.I)),
]


def classify(title: str) -> str:
    for event_type, pattern in CLASSIFICATION_RULES:
        if pattern.search(title):
            return event_type
    return "other_psx_notice"


def parse_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), "%b %d, %Y").strftime("%Y-%m-%d")


def parse_time(raw: str) -> str | None:
    try:
        return datetime.strptime(raw.strip(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        return None


def parse_attachments(cell) -> dict:
    attachments = {"pdf_url": None, "image_url": None}
    for a in cell.find_all("a"):
        href = a.get("href", "")
        if href.startswith("/download/") and href.endswith(".pdf"):
            attachments["pdf_url"] = BASE_URL + href
        elif a.get("data-images"):
            attachments["image_url"] = IMAGE_BASE_URL + a["data-images"]
    return attachments


def parse_row(tr) -> dict | None:
    cells = tr.find_all("td")
    if len(cells) < 4:
        return None

    date_iso = parse_date(cells[0].get_text(strip=True))
    time_24h = parse_time(cells[1].get_text(strip=True))
    title = cells[2].get_text(strip=True)
    attachments = parse_attachments(cells[3])

    event_type = classify(title)

    # extract a company name from the title where present, since IPO
    # notices name the company in free text ("Initial Public Offering of
    # Symmetry Group Limited") even though there's no symbol column to
    # key off -- best-effort, not always present
    company_match = re.search(r"(?:of|for)\s+([A-Z][A-Za-z0-9.\-&' ]+(?:Limited|Ltd\.?))",
                               title)
    company_name = company_match.group(1).strip() if company_match else None

    return {
        "id": f"psx-notice-{date_iso}-{time_24h or '0000'}".replace(":", ""),
        "type": event_type,
        "status": "confirmed",
        "date": date_iso,
        # same reasoning as parse_announcements.py -- this is filing time,
        # not scheduled event time. See that file's comment for detail.
        "time": None,
        "scope": "market",
        "symbol": None,
        "sector": None,
        "company_name": company_name,
        "title": title,
        "source_url": attachments["pdf_url"] or attachments["image_url"],
        "payload": {
            "pdf_url": attachments["pdf_url"],
            "image_url": attachments["image_url"],
            "filed_time": time_24h,
        },
        "revision_of": None,
    }


def parse_psx_notices_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("tbody.tbl__body tr")

    events = []
    skipped = 0
    for tr in rows:
        try:
            e = parse_row(tr)
            if e is not None:
                events.append(e)
        except (ValueError, AttributeError, IndexError) as err:
            skipped += 1
            print(f"WARNING: skipped malformed notice row: {err}", file=sys.stderr)

    if skipped:
        print(f"INFO: {skipped}/{len(rows)} rows skipped as malformed", file=sys.stderr)

    ipo_related = [e for e in events if e["type"].startswith("ipo_")]
    if ipo_related:
        print(f"INFO: {len(ipo_related)} IPO-related notices found: "
              f"{[(e['date'], e['type'], e['title'][:50]) for e in ipo_related]}",
              file=sys.stderr)

    return events


if __name__ == "__main__":
    with open("fixtures/psx_notices_sample.html") as f:
        html = f.read()
    events = parse_psx_notices_html(html)
    json.dump(events, sys.stdout, indent=2)
