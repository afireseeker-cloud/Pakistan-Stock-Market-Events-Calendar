"""
Parses the raw HTML fragment returned by PSX's /announcements endpoint
(a server-rendered <tbody class="tbl__body"> table, NOT JSON) into typed
calendar events.

Confirmed markup shape (from live DevTools capture, 11-Aug-2026):
  <tr>
    <td>Aug 11, 2026</td>                                  -- date
    <td>3:29 PM</td>                                       -- time
    <td><a class="tbl__symbol" href="/company/EPQL">...    -- symbol
    <td><a class="tbl__symbol" href="/company/EPQL">...    -- company name
    <td>Board Meeting ... <div class="tag ...">REVOKED</div></td>  -- title
                                                               (+ optional
                                                               inline REVOKED
                                                               tag)
    <td><a href="/download/document/X.pdf">PDF</a>          -- attachments:
        <a data-images="X.gif">View</a></td>                   PDF (direct
                                                                 href) and/or
                                                                 GIF (JS-only,
                                                                 base URL is
                                                                 /download/image/)

Known limitation: this table gives the announcement TITLE only, never the
full agenda text. Some companies fold agenda detail into the title itself
("Board Meeting for the Announcement of Financial Results for..."); most
don't ("Board Meeting", "217th Board Meeting of Soneri Bank Limited"). Real
agenda extraction requires a second stage reading the linked PDF (text
extraction) or GIF (OCR) -- out of scope here, flagged in payload instead.
"""
import json
import re
import sys
from datetime import datetime

from bs4 import BeautifulSoup

BASE_URL = "https://dps.psx.com.pk"
IMAGE_BASE_URL = "https://dps.psx.com.pk/download/image/"

CLASSIFICATION_RULES = [
    ("book_closure", re.compile(r"closure of share transfer books|book closure", re.I)),
    # explicit exclusion, must come before the results rule below -- PSX
    # titles like "Board Meeting Other Than Financial Results" (real
    # example, PSO's own filings) contain "financial results" as a
    # substring while explicitly saying the meeting ISN'T about results.
    # Without this, the results rule below would misclassify it.
    ("board_meeting", re.compile(r"board meeting.*other than.*results|other than.*financial results", re.I)),
    ("agm", re.compile(r"\b(agm|annual general meeting|eogm|extraordinary general meeting)\b", re.I)),
    ("dividend", re.compile(r"dividend", re.I)),
    # checked BEFORE the generic board_meeting rule below -- real PSX
    # titles very commonly combine both ("Board Meeting for the
    # Announcement of Financial Results for..."), and the actual content
    # (results) matters more than the mechanism (a board meeting produced
    # it) for routing to the right calendar tab. Without this ordering,
    # every such title silently lands as a generic board_meeting and never
    # reaches the Earnings tab at all -- confirmed against real captured
    # data (ENGROH's own filing uses exactly this phrasing).
    ("results", re.compile(
        r"financial results|quarterly results|annual results|"
        r"financial statements|quarterly financial|"
        r"quarterly report|half.?yearly report|annual report", re.I)),
    ("board_meeting", re.compile(r"board meeting", re.I)),
    ("corporate_briefing", re.compile(r"corporate briefing session", re.I)),
    ("director_disclosure", re.compile(r"disclosure of interest", re.I)),
    ("governance_change", re.compile(
        r"appointment of director|resignation of director|"
        r"change of company secretary|change of.*chief financial", re.I)),
]

CANCELLATION_PATTERNS = re.compile(r"calling off|cancell?ed|withdrawn", re.I)

# REVOKED only maps to event-level cancellation for types where the
# announcement itself IS the scheduled thing being called off. For every
# other type (results, disclosures, governance changes...) a REVOKED tag
# means PSX retracted/reissued that specific announcement -- the underlying
# fact (a discovery, a filing) still stands. Conflating the two produces
# false cancellations, e.g. a revoked-and-reissued results announcement or
# a revoked press release about a hydrocarbon discovery, neither of which
# means the event didn't happen.
SCHEDULE_BEARING_TYPES = {"board_meeting", "agm", "book_closure", "corporate_briefing"}

# Even within a schedule-bearing type, REVOKED on a backward-looking title
# ("Minutes of...", "...in Progress") means PSX retracted that filing after
# the fact -- not that the underlying event was cancelled. Only forward-
# looking titles (a notice of something upcoming) get treated as a real
# cancellation when revoked.
BACKWARD_LOOKING_PATTERNS = re.compile(r"minutes of|in progress|video recording", re.I)


def classify(title: str) -> str:
    for event_type, pattern in CLASSIFICATION_RULES:
        if pattern.search(title):
            return event_type
    return "other_announcement"


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


def parse_row(tr) -> dict:
    cells = tr.find_all("td")
    if len(cells) < 6:
        return None

    date_iso = parse_date(cells[0].get_text(strip=True))
    time_24h = parse_time(cells[1].get_text(strip=True))

    symbol_link = cells[2].find("a")
    symbol = symbol_link.get_text(strip=True) if symbol_link else None

    company_link = cells[3].find("a")
    company_name = company_link.get_text(strip=True) if company_link else None

    # title cell may contain an inline status tag <div class="tag ...">TEXT</div>
    # -- PSX uses more than one value here (REVOKED, REVISED, ...); capture
    # whatever it says rather than assuming REVOKED is the only variant.
    title_cell = cells[4]
    status_tag_div = title_cell.find("div", class_="tag")
    status_tag_text = status_tag_div.get_text(strip=True).upper() if status_tag_div else None
    is_revoked = status_tag_text == "REVOKED"
    if status_tag_div:
        status_tag_div.insert_before(" ")  # preserve word boundary before removing
        status_tag_div.extract()
    title = " ".join(title_cell.get_text(strip=True).split())  # collapse whitespace

    event_type = classify(title)
    is_title_cancellation = bool(CANCELLATION_PATTERNS.search(title))
    is_backward_looking = bool(BACKWARD_LOOKING_PATTERNS.search(title))
    # only flip status for schedule-bearing, forward-looking titles -- see
    # SCHEDULE_BEARING_TYPES / BACKWARD_LOOKING_PATTERNS notes above
    is_cancelled = ((is_revoked or is_title_cancellation)
                     and event_type in SCHEDULE_BEARING_TYPES
                     and not is_backward_looking)
    status = "cancelled" if is_cancelled else "confirmed"

    attachments = parse_attachments(cells[5])
    has_agenda_in_title = event_type == "board_meeting" and bool(re.search(
        r"for the announcement of|regarding", title, re.I))

    return {
        "id": f"psx-annc-{symbol}-{date_iso}-{time_24h or '0000'}".replace(":", ""),
        "type": event_type,
        "status": status,
        "date": date_iso,
        # NOT the scheduled event time -- this is when PSX filed the notice,
        # which is a different thing from when a board meeting/results
        # announcement is actually scheduled. Showing it as the event's
        # "time" was misleading (a notice filed at 3:29pm about a meeting
        # happening next week isn't a 3:29pm event). filed_time in payload
        # preserves it for anyone who wants it; the top-level time field
        # stays honestly blank since we don't have the real scheduled time.
        "time": None,
        "scope": "company" if symbol else "market",
        "symbol": symbol,
        "sector": None,
        "title": title,
        "company_name": company_name,
        "source_url": attachments["pdf_url"] or attachments["image_url"],
        "payload": {
            "pdf_url": attachments["pdf_url"],
            "image_url": attachments["image_url"],
            "filed_time": time_24h,
            "agenda_in_title": has_agenda_in_title,
            "agenda_extracted": False,  # true once a v2 PDF/OCR stage runs
            "announcement_revoked": is_revoked,  # true even when status stays "confirmed"
            "raw_status_tag": status_tag_text,  # None, "REVOKED", "REVISED", etc.
        },
        "revision_of": None,
    }


def parse_announcements_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("tbody.tbl__body tr")
    events = [parse_row(tr) for tr in rows]
    events = [e for e in events if e is not None]

    unclassified = [e for e in events if e["type"] == "other_announcement"]
    if unclassified:
        print(f"INFO: {len(unclassified)}/{len(events)} rows unclassified "
              f"(expected -- most PSX announcements are disclosure noise, "
              f"not calendar events): {[e['title'][:50] for e in unclassified[:3]]}",
              file=sys.stderr)

    cancelled = [e for e in events if e["status"] == "cancelled"]
    if cancelled:
        print(f"INFO: {len(cancelled)} cancelled/revoked events detected: "
              f"{[(e['symbol'], e['title'][:40]) for e in cancelled]}",
              file=sys.stderr)

    return events


if __name__ == "__main__":
    with open("fixtures/announcements_sample.html") as f:
        html = f.read()
    events = parse_announcements_html(html)
    json.dump(events, sys.stdout, indent=2)
