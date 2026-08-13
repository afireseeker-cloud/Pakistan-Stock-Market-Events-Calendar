"""
Scrapes each company's own website domain from their PSX profile page
(dps.psx.com.pk/company/{symbol}), building a cache that a separate script
turns into real logo URLs.

Unlike /announcements or /payouts, this page is plain server-rendered HTML
(confirmed earlier -- a direct fetch returned full content, no AJAX/JS
rendering step needed), so this is a simple GET, no POST/session discovery
required.

*** IMPORTANT CAVEAT, read before running at scale ***
The WEBSITE-field extraction below is built from a markdown-flattened
render of one real page (PSO), not raw HTML I've actually inspected --
every other parser in this project needed at least one round of fixing
once real HTML came back, and there's no reason to expect this one is
different. Run with --debug against 2-3 symbols FIRST and check the output
makes sense before running against your full symbol list. If it comes back
empty, the fix is almost certainly a selector adjustment, not a rewrite --
paste the debug output and I'll adjust it the same way we fixed every
other parser in this project.

Symbol source: reads unique symbols out of a merged events file by default
(only scrapes companies that actually appear in your calendar, not all
~500 PSX-listed companies), or accepts an explicit --symbols list.

Run:
    python3 fetch_company_domains.py --from-events events_merged.json --debug
    python3 fetch_company_domains.py --from-events events_merged.json
    python3 fetch_company_domains.py --symbols LUCK,UBL,FCCL
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

CACHE_FILE = "company_domains_cache.json"
RATE_LIMIT_SECONDS = 0.5
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# domains that are PSX's own chrome or social links, never a company's real
# site -- if extraction ever accidentally grabs one of these, treat it as
# a miss rather than caching a wrong domain
EXCLUDED_DOMAINS = {
    "psx.com.pk", "dps.psx.com.pk", "facebook.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "capitalstake.com", "sdms.secp.gov.pk",
    "pucars.com", "knowledgecenter.psx.com.pk",
}


def extract_website(soup: BeautifulSoup) -> str | None:
    """Finds the company's own website URL on their profile page.

    Confirmed real markup (from a live capture, PSO's page):
        <div class="item__head">WEBSITE</div>
        <p> <a href="http://www.psopk.com" target="_blank">www.psopk.com</a></p>

    Primary strategy targets that exact class directly. A looser page-wide
    text search was tried first and produced a real false positive: PSX's
    own search bar has <option value="website">Website</option> (a "search
    by website" mode), which a case-insensitive text match on "WEBSITE"
    matches too -- and since it appears earlier in the page than the real
    Company Profile section, the old code anchored there and never found
    the real field. Scoping to the confirmed class eliminates that
    collision entirely.
    """
    for head in soup.find_all(class_="item__head"):
        if head.get_text(strip=True).upper() == "WEBSITE":
            value_p = head.find_next_sibling("p")
            if value_p:
                a = value_p.find("a", href=True)
                if a:
                    href = a["href"].strip()
                    if href.startswith("http"):
                        domain = urlparse(href).netloc.replace("www.", "")
                        if domain and not any(excluded in domain for excluded in EXCLUDED_DOMAINS):
                            return href

    # fallback: looser page-wide search, in case some company pages use
    # different markup than PSO's -- unlikely for a templated site, but
    # cheap insurance rather than returning None outright
    label = None
    for tag in soup.find_all(string=re.compile(r"^\s*WEBSITE\s*$", re.I)):
        parent = tag.parent
        if parent.name == "option":  # the search-bar dropdown false positive
            continue
        label = parent
        break
    if label is None:
        return None

    candidates = []
    candidates.extend(label.find_all("a", href=True))
    node = label
    for _ in range(4):
        if node is None:
            break
        nxt = node.find_next_sibling()
        if nxt:
            candidates.extend(nxt.find_all("a", href=True) if nxt.name != "a" else [nxt])
            node = nxt
        else:
            node = node.parent

    for a in candidates:
        href = a.get("href", "").strip()
        if not href.startswith("http"):
            continue
        domain = urlparse(href).netloc.replace("www.", "")
        if domain and not any(excluded in domain for excluded in EXCLUDED_DOMAINS):
            return href

    return None


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
    symbols = sorted({e["symbol"] for e in events if e.get("symbol")})
    return symbols


def load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-events", help="merged events JSON file to extract symbols from")
    ap.add_argument("--symbols", help="comma-separated symbol list, alternative to --from-events")
    ap.add_argument("--debug", action="store_true",
                     help="verbose per-symbol output, recommended for the first run")
    ap.add_argument("--limit", type=int, default=None,
                     help="only process the first N symbols -- use with --debug to sanity-check before a full run")
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_events:
        symbols = symbols_from_events(args.from_events)
    else:
        print("ERROR: provide --from-events or --symbols", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        symbols = symbols[:args.limit]

    cache = load_cache()
    to_fetch = [s for s in symbols if s not in cache]
    print(f"INFO: {len(symbols)} symbols total, {len(symbols) - len(to_fetch)} already "
          f"cached, {len(to_fetch)} to fetch", file=sys.stderr)

    session = requests.Session()
    resolved_count = 0

    for i, symbol in enumerate(to_fetch):
        html = fetch_company_page(session, symbol)
        if html is None:
            cache[symbol] = {"website": None, "domain": None, "resolved": False}
            continue

        soup = BeautifulSoup(html, "lxml")
        website = extract_website(soup)

        if website:
            domain = urlparse(website).netloc.replace("www.", "")
            cache[symbol] = {"website": website, "domain": domain, "resolved": True}
            resolved_count += 1
            if args.debug:
                print(f"DEBUG: {symbol} -> {website} (domain: {domain})", file=sys.stderr)
        else:
            cache[symbol] = {"website": None, "domain": None, "resolved": False}
            if args.debug:
                print(f"DEBUG: {symbol} -> no website found. First 300 chars of page:\n"
                      f"{html[:300]}", file=sys.stderr)

        if (i + 1) % 25 == 0:
            print(f"INFO: {i + 1}/{len(to_fetch)} processed, {resolved_count} resolved so far",
                  file=sys.stderr)
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)  # checkpoint periodically

        time.sleep(RATE_LIMIT_SECONDS)

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    total_resolved = sum(1 for v in cache.values() if v.get("resolved"))
    print(f"\nDONE: {total_resolved}/{len(cache)} symbols have a resolved website domain, "
          f"written to {CACHE_FILE}", file=sys.stderr)
    if to_fetch and resolved_count == 0:
        print("\nWARNING: zero symbols resolved in this run -- the WEBSITE-field selector "
              "is very likely wrong for the real page structure. Re-run with --debug "
              "--limit 3 and paste the output so the extraction logic can be fixed "
              "against real HTML.", file=sys.stderr)


if __name__ == "__main__":
    main()
