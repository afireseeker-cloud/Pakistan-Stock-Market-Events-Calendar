"""
Fetches real EPS data from each company's PSX profile page (the
"Financials" section -- see parse_financials.py for the extraction logic
and its licensing note about Capital Stake).

Same page type fetch_company_domains.py already scrapes for website
domains -- plain server-rendered HTML, no AJAX discovery needed. Only
scrapes symbols that actually appear in your merged calendar, not all
~500 PSX-listed companies.

Cached by symbol in eps_data_cache.json -- re-running only fetches symbols
not already cached. Note this is company-level data (a company's current
known EPS), not tied to any specific announcement's exact reporting
period -- attach_eps.py applies the same latest-known figures to every
"results" event for that symbol, which is a deliberate simplification
(see attach_eps.py's docstring) rather than precisely matching each
specific announcement to its exact covered period.

Run:
    python3 fetch_eps_data.py --from-events events_merged.json
    python3 fetch_eps_data.py --from-events events_merged.json --debug --limit 5
"""
import argparse
import json
import sys
import time

import requests
from bs4 import BeautifulSoup

from parse_financials import extract_financials, compute_eps_summary

CACHE_FILE = "eps_data_cache.json"
RATE_LIMIT_SECONDS = 0.5
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def fetch_company_page(session: requests.Session, symbol: str) -> str | None:
    url = f"https://dps.psx.com.pk/company/{symbol}"
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"WARNING: {symbol} fetch failed (attempt {attempt + 1}/{MAX_RETRIES}): "
                  f"{e}. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    print(f"WARNING: giving up on {symbol} after {MAX_RETRIES} attempts", file=sys.stderr)
    return None


def symbols_from_events(path: str) -> list[str]:
    with open(path) as f:
        events = json.load(f)
    return sorted({e["symbol"] for e in events if e.get("symbol")})


def load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-events", required=True, help="merged events JSON to extract symbols from")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                     help="only process first N symbols -- sanity check before a full run")
    args = ap.parse_args()

    symbols = symbols_from_events(args.from_events)
    if args.limit:
        symbols = symbols[:args.limit]

    cache = load_cache()
    to_fetch = [s for s in symbols if s not in cache]
    print(f"INFO: {len(symbols)} symbols total, {len(symbols) - len(to_fetch)} already "
          f"cached, {len(to_fetch)} to fetch", file=sys.stderr)

    session = requests.Session()
    resolved = 0

    for i, symbol in enumerate(to_fetch):
        html = fetch_company_page(session, symbol)
        if html is None:
            cache[symbol] = {"resolved": False}
            continue

        soup = BeautifulSoup(html, "lxml")
        financials = extract_financials(soup)

        if financials and (financials["annual"] or financials["quarterly"]):
            summary = compute_eps_summary(financials)
            cache[symbol] = {"resolved": True, **summary}
            resolved += 1
            if args.debug:
                print(f"DEBUG: {symbol} -> {summary}", file=sys.stderr)
        else:
            cache[symbol] = {"resolved": False}
            if args.debug:
                print(f"DEBUG: {symbol} -> no financials section found", file=sys.stderr)

        if (i + 1) % 25 == 0:
            print(f"INFO: {i + 1}/{len(to_fetch)} processed, {resolved} resolved so far",
                  file=sys.stderr)
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)

        time.sleep(RATE_LIMIT_SECONDS)

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    total_resolved = sum(1 for v in cache.values() if v.get("resolved"))
    print(f"\nDONE: {total_resolved}/{len(cache)} symbols have EPS data, "
          f"written to {CACHE_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
