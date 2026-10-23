
"""
Fetches real EPS data from each company's PSX profile page.

EPS data is cached by symbol in eps_data_cache.json.

Refresh policy:
- Successfully resolved entries: refresh every 7 days.
- Failed or unresolved entries: retry after 24 hours.
- Legacy entries without fetched_at: refresh once.
- Network failures preserve existing cache data where available.

Run:
    python3 fetch_eps_data.py --from-events events_merged.json
    python3 fetch_eps_data.py --from-events events_merged.json --debug --limit 5
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from psx_relay import via_relay
from parse_financials import extract_financials, compute_eps_summary


CACHE_FILE = "eps_data_cache.json"
RATE_LIMIT_SECONDS = 0.5
MAX_RETRIES = 3

REFRESH_AFTER_DAYS = 1
RETRY_AFTER_HOURS = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_utc() -> str:
    return now_utc().isoformat(timespec="seconds")


def should_fetch(entry: dict, now: datetime) -> bool:
    """Return True when an entry is due for refresh or retry."""
    fetched_at = entry.get("fetched_at")

    # Legacy entries without a timestamp are refreshed once.
    if not fetched_at:
        return True

    try:
        fetched = datetime.fromisoformat(
            fetched_at.replace("Z", "+00:00")
        )
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True

    age_hours = (now - fetched).total_seconds() / 3600

    # Failed and unresolved entries are retried after 24 hours.
    if not entry.get("resolved"):
        return age_hours >= RETRY_AFTER_HOURS

    # Successful entries are refreshed every 7 days.
    return age_hours >= REFRESH_AFTER_DAYS * 24


def fetch_company_page(
    session: requests.Session, symbol: str
) -> str | None:
    url = via_relay(f"https://dps.psx.com.pk/company/{symbol}")

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(
                f"WARNING: {symbol} fetch failed "
                f"(attempt {attempt + 1}/{MAX_RETRIES}): "
                f"{e}. Retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)

    print(
        f"WARNING: giving up on {symbol} after {MAX_RETRIES} attempts",
        file=sys.stderr,
    )
    return None


def symbols_from_events(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        events = json.load(f)

    return sorted({
        e["symbol"]
        for e in events
        if e.get("symbol")
    })


def load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
            return cache if isinstance(cache, dict) else {}
    except FileNotFoundError:
        return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--from-events",
        required=True,
        help="merged events JSON to extract symbols from",
    )
    ap.add_argument("--debug", action="store_true")
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only process first N symbols -- sanity check before a full run",
    )
    args = ap.parse_args()

    symbols = symbols_from_events(args.from_events)

    if args.limit is not None:
        symbols = symbols[:max(0, args.limit)]

    cache = load_cache()
    now = now_utc()

    to_fetch = [
        symbol for symbol in symbols
        if should_fetch(cache.get(symbol, {}), now)
    ]

    print(
        f"INFO: {len(symbols)} symbols total, "
        f"{len(symbols) - len(to_fetch)} not due, "
        f"{len(to_fetch)} to fetch or retry",
        file=sys.stderr,
    )

    session = requests.Session()
    resolved = 0
    failed = 0

    for i, symbol in enumerate(to_fetch, start=1):
        previous = cache.get(symbol, {})
        html = fetch_company_page(session, symbol)

        if html is None:
            # Preserve a previously successful EPS result if refresh fails.
            if previous.get("resolved"):
                cache[symbol] = {
                    **previous,
                    "last_attempt_at": timestamp_utc(),
                }
            else:
                cache[symbol] = {
                    **previous,
                    "resolved": False,
                    "last_attempt_at": timestamp_utc(),
                    "fetched_at": timestamp_utc(),
                }

            failed += 1

        else:
            soup = BeautifulSoup(html, "lxml")
            financials = extract_financials(soup)

            if financials and (
                financials.get("annual") or financials.get("quarterly")
            ):
                summary = compute_eps_summary(financials)

                cache[symbol] = {
                    "resolved": True,
                    **summary,
                    "fetched_at": timestamp_utc(),
                    "last_attempt_at": timestamp_utc(),
                }
                resolved += 1

                if args.debug:
                    print(
                        f"DEBUG: {symbol} -> {summary}",
                        file=sys.stderr,
                    )

            else:
                # A successful page request with no usable financial data
                # should be retried later, but must not destroy prior EPS.
                if previous.get("resolved"):
                    cache[symbol] = {
                        **previous,
                        "last_attempt_at": timestamp_utc(),
                    }
                    if args.debug:
                        print(
                            f"DEBUG: {symbol} -> no financials found; "
                            "preserved previous successful data",
                            file=sys.stderr,
                        )
                else:
                    cache[symbol] = {
                        "resolved": False,
                        "fetched_at": timestamp_utc(),
                        "last_attempt_at": timestamp_utc(),
                    }

                failed += 1

        if i % 25 == 0:
            print(
                f"INFO: {i}/{len(to_fetch)} processed, "
                f"{resolved} refreshed successfully, "
                f"{failed} unsuccessful",
                file=sys.stderr,
            )
            save_cache(cache)

        time.sleep(RATE_LIMIT_SECONDS)

    save_cache(cache)

    total_resolved = sum(
        1 for entry in cache.values()
        if entry.get("resolved")
    )

    print(
        f"\nDONE: {total_resolved}/{len(cache)} symbols have EPS data. "
        f"{resolved} refreshed successfully; {failed} unsuccessful. "
        f"Written to {CACHE_FILE}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
