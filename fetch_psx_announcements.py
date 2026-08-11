"""
Fetch harness for PSX's /announcements endpoint. This is meant to run on a
real server/cron box, NOT in this sandbox -- dps.psx.com.pk isn't reachable
from here (no network access to it). Drop this alongside parse_announcements.py
and normalize_announcements.py.

Confirmed request shape (from the person's DevTools capture):
    GET https://dps.psx.com.pk/announcements
        ?type=C              -- C=Companies Announcements (also: A=CDC, B=SECP,
                                 D=NCCPL, E=PSX Notices -- see <select name="type">
                                 in the page's own form)
        &symbol=EFERT         -- optional, omit for market-wide
        &query=financial       -- optional keyword filter
        &count=50               -- page size PSX itself uses in its own UI
        &offset=0                -- pagination cursor, advance by `count` each page
        &date_from=2026-07-01
        &date_to=2026-07-31
        &page=annc

NOT YET CONFIRMED: whether this is actually a GET with these as query params
(as pasted) or whether the real browser call is a POST with a form-encoded
body of the same fields -- the person's DevTools screenshot showed the
Preview/Response tabs but not the Headers > Request Method. Check that
before relying on this in production; the code below tries GET first since
that's what was pasted, with a POST fallback path stubbed but untested.

Etiquette borrowed from the mtauha/psxdata project (the only other PSX
scraper that documents its approach): persistent session, realistic headers,
2 req/sec rate limit, exponential backoff. Given PSX's data-license notice
(see prior conversation), this is built for the person's own personal/dev
use while a licensing conversation with PSX's market data team is pending --
not licensed for redistribution at scale.
"""
import argparse
import sys
import time
from datetime import date, timedelta

import requests

from parse_announcements import parse_announcements_html
from parse_psx_notices import parse_psx_notices_html

BASE_URL = "https://dps.psx.com.pk/announcements"
PAGE_SIZE = 50
RATE_LIMIT_SECONDS = 0.5  # 2 req/sec, matches psxdata's documented limiter
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://dps.psx.com.pk/announcements/companies",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_page(session: requests.Session, *, date_from: str, date_to: str,
                offset: int, announcement_type: str = "C",
                symbol: str = "", keyword: str = "", debug: bool = False) -> str:
    params = {
        "type": announcement_type,
        "symbol": symbol,
        "query": keyword,
        "count": PAGE_SIZE,
        "offset": offset,
        "date_from": date_from,
        "date_to": date_to,
        "page": "annc",
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            # POST with form-encoded body -- the pasted DevTools capture
            # (URL on one line, ampersand-joined key=value on the next) is
            # how Chrome renders a POST payload, not a GET query string.
            resp = session.post(BASE_URL, data=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            if debug:
                print(f"DEBUG: status={resp.status_code} "
                      f"content-type={resp.headers.get('content-type')} "
                      f"len={len(resp.text)}", file=sys.stderr)
                print(f"DEBUG: first 500 chars:\n{resp.text[:500]}", file=sys.stderr)
            return resp.text
        except requests.RequestException as e:
            last_error = e
            wait = 2 ** attempt
            print(f"WARNING: fetch failed (attempt {attempt + 1}/{MAX_RETRIES}): "
                  f"{e}. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Giving up after {MAX_RETRIES} attempts: {last_error}")


def fetch_all_pages(session: requests.Session, *, date_from: str, date_to: str,
                     announcement_type: str = "C", symbol: str = "",
                     keyword: str = "", max_pages: int = 200,
                     debug: bool = False) -> list[str]:
    """Paginates until a page returns fewer rows than PAGE_SIZE, or max_pages hit.
    max_pages is a safety valve -- 200 pages * 50 rows = 10,000 rows ceiling
    per call, well above what a sane date-range query should ever need."""
    html_pages = []
    offset = 0

    for page_num in range(max_pages):
        html = fetch_page(session, date_from=date_from, date_to=date_to,
                           offset=offset, announcement_type=announcement_type,
                           symbol=symbol, keyword=keyword,
                           debug=(debug and page_num == 0))
        html_pages.append(html)

        row_count = html.count("<tr>")
        print(f"INFO: page {page_num + 1} (offset={offset}): {row_count} rows",
              file=sys.stderr)

        if row_count < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_LIMIT_SECONDS)
    else:
        print(f"WARNING: hit max_pages={max_pages} without a short page -- "
              f"date range may have more data than fetched. Narrow the range "
              f"and re-run, or raise max_pages.", file=sys.stderr)

    return html_pages


def daterange_chunks(start: date, end: date, chunk_days: int = 31):
    """PSX's own UI queries by month; chunking avoids hammering one giant
    request and keeps each ingestion run resumable if it fails partway."""
    d = start
    while d <= end:
        chunk_end = min(d + timedelta(days=chunk_days - 1), end)
        yield d, chunk_end
        d = chunk_end + timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True,
                     help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbol", default="", help="omit for market-wide")
    ap.add_argument("--type", dest="announcement_type", default="C",
                     help="C=Companies (default), A=CDC, B=SECP, D=NCCPL, E=PSX")
    ap.add_argument("--out", default="events_psx_announcements.jsonl",
                     help="output path, JSON Lines format")
    ap.add_argument("--debug", action="store_true",
                     help="print raw response status/headers/snippet for the first page")
    args = ap.parse_args()

    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)

    session = requests.Session()
    all_events = []

    # PSX Notices (type=E) uses a different 4-column table schema than
    # Companies Announcements (type=C, and the other types) -- route to
    # the matching parser rather than force everything through one that
    # assumes a SYMBOL/NAME column that type=E doesn't have.
    parser = parse_psx_notices_html if args.announcement_type == "E" else parse_announcements_html

    for chunk_start, chunk_end in daterange_chunks(start, end):
        print(f"INFO: fetching {chunk_start} to {chunk_end}...", file=sys.stderr)
        html_pages = fetch_all_pages(
            session,
            date_from=chunk_start.isoformat(),
            date_to=chunk_end.isoformat(),
            announcement_type=args.announcement_type,
            symbol=args.symbol,
            debug=args.debug,
        )
        for html in html_pages:
            all_events.extend(parser(html))

    # dedupe: same symbol+date+time+title can appear across overlapping
    # chunk boundaries if PSX's offset pagination shifts mid-run
    seen = set()
    deduped = []
    for e in all_events:
        key = (e["symbol"], e["date"], e["time"], e["title"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    with open(args.out, "w") as f:
        for e in deduped:
            f.write(__import__("json").dumps(e) + "\n")

    print(f"\nDONE: {len(deduped)} unique events written to {args.out} "
          f"({len(all_events) - len(deduped)} duplicates dropped)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
