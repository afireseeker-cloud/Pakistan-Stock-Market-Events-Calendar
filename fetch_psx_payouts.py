"""
Fetch harness for PSX's /payouts endpoint, market-wide. Confirmed:
empty symbol="" returns all companies rather than requiring a per-symbol
loop -- much simpler than /announcements.

Unlike /announcements, the payouts endpoint takes no date_from/date_to --
it appears to return each company's payout history in full (LUCK's sample
response included both its 2026 and 2025 book closures), so this just
paginates by offset/count with no date chunking.

Run: python3 fetch_psx_payouts.py --out events_psx_payouts.jsonl
"""
import argparse
import sys
import time

import requests

from parse_payouts import parse_payouts_html

BASE_URL = "https://dps.psx.com.pk/payouts"
PAGE_SIZE = 25  # matches PSX's own default (seen in the captured payload)
RATE_LIMIT_SECONDS = 0.5
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://dps.psx.com.pk/payouts",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_page(session: requests.Session, *, offset: int, symbol: str = "",
                debug: bool = False) -> str:
    params = {"symbol": symbol, "count": PAGE_SIZE, "offset": offset}

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(BASE_URL, data=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            if debug:
                print(f"DEBUG: status={resp.status_code} len={len(resp.text)}",
                      file=sys.stderr)
                print(f"DEBUG: first 500 chars:\n{resp.text[:500]}", file=sys.stderr)
            return resp.text
        except requests.RequestException as e:
            last_error = e
            wait = 2 ** attempt
            print(f"WARNING: fetch failed (attempt {attempt + 1}/{MAX_RETRIES}): "
                  f"{e}. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Giving up after {MAX_RETRIES} attempts: {last_error}")


def fetch_all_pages(session: requests.Session, *, symbol: str = "",
                     max_pages: int = 2000, debug: bool = False) -> list[str]:
    """max_pages=2000 * 25/page = 50,000 row ceiling -- payouts across the
    full market's dividend history could genuinely be large; raise if
    warned below."""
    html_pages = []
    offset = 0

    for page_num in range(max_pages):
        html = fetch_page(session, offset=offset, symbol=symbol,
                           debug=(debug and page_num == 0))
        html_pages.append(html)

        row_count = html.count("<tr>")
        if page_num % 20 == 0:  # payouts can run to many pages, don't spam
            print(f"INFO: page {page_num + 1} (offset={offset}): {row_count} rows",
                  file=sys.stderr)

        if row_count < PAGE_SIZE:
            print(f"INFO: reached last page at offset={offset} ({row_count} rows)",
                  file=sys.stderr)
            break

        offset += PAGE_SIZE
        time.sleep(RATE_LIMIT_SECONDS)
    else:
        print(f"WARNING: hit max_pages={max_pages} without a short page -- "
              f"there may be more data. Raise max_pages and re-run.",
              file=sys.stderr)

    return html_pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="", help="omit for market-wide")
    ap.add_argument("--out", default="events_psx_payouts.jsonl")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    session = requests.Session()
    html_pages = fetch_all_pages(session, symbol=args.symbol, debug=args.debug)

    all_events = []
    for html in html_pages:
        all_events.extend(parse_payouts_html(html))

    # dedupe on the event's own id -- already unique per event (built from
    # symbol+book_closure_start for confirmed closures, symbol+announcement_date
    # for pending ones), so this works across both event shapes without
    # needing to know which payload fields a given type actually has
    seen = set()
    deduped = []
    for e in all_events:
        if e["id"] not in seen:
            seen.add(e["id"])
            deduped.append(e)

    with open(args.out, "w") as f:
        for e in deduped:
            f.write(__import__("json").dumps(e) + "\n")

    print(f"\nDONE: {len(deduped)} unique payout events written to {args.out} "
          f"({len(all_events) - len(deduped)} duplicates dropped)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
