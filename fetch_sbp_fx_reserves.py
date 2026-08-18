"""
Scrapes SBP's homepage for the live "Liquid Foreign Exchange Reserves"
snapshot and confirms the matching predicted event with real numbers.

IMPORTANT CAVEAT, same shape as fetch_company_domains.py's original
WEBSITE-field situation: I only have markdown-rendered content for this
page, not raw HTML (my fetch tool converts HTML to markdown, discarding
the actual tag/class structure). The confirmed real values on that render
were:
    As on 07-August-2026: SBP's Reserves 17,057.0, Bank's Reserves 5,441.3,
    Total Reserves 22,498.3
This script extracts by TEXT PATTERN (label text followed by a number),
not by assumed CSS selectors -- more resilient to unknown exact markup
than a tag-based approach would be, but still genuinely unverified against
the real raw HTML. First real run against this script is the actual test,
same as every other "couldn't get raw HTML" situation in this project.

WEEK-OVER-WEEK COMPARISON DESIGN: the homepage only ever shows the current
week's snapshot, never history -- there's no separate page to scrape for
"last week's number". Instead, this script builds its own history cache
(fx_reserves_history.json) over time: each confirmed week's value gets
stored, and the following week's "prior" comes from what THIS script
itself captured the week before. The very first run has nothing to
compare against yet, so prior_value stays honestly null rather than
guessed -- it self-populates from the second real run onward.

Run:
    python3 fetch_sbp_fx_reserves.py --debug              # verify the snapshot, no events file needed
    python3 fetch_sbp_fx_reserves.py events_merged.json --out events_merged.json
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

HISTORY_FILE = "fx_reserves_history.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# Matches "As on 07- August - 2026" / "As on 07-August-2026" / similar
# spacing variants -- SBP's own rendering has inconsistent spacing around
# the dashes based on what was observed
DATE_PATTERN = re.compile(r"As\s*on\s*[:\-]?\s*(\d{1,2})\s*-?\s*([A-Za-z]+)\s*-?\s*(\d{4})", re.I)


def extract_number_after(text: str, label_pattern: str) -> float | None:
    m = re.search(label_pattern + r"\s*[:\n]*\s*([\d,]+\.?\d*)", text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_current_snapshot(session: requests.Session):
    resp = session.get("https://www.sbp.org.pk/", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    text = soup.get_text(separator="\n")

    date_match = DATE_PATTERN.search(text)
    if not date_match:
        return None

    try:
        day, month_name, year = date_match.groups()
        report_date = datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()
    except ValueError:
        return None

    sbp_reserves = extract_number_after(text, r"SBP.s\s*Reserves")
    bank_reserves = extract_number_after(text, r"Bank.s\s*Reserves")
    total_reserves = extract_number_after(text, r"Total\s*Reserves")

    if sbp_reserves is None:
        return None  # couldn't find the real numbers, don't guess

    return {
        "report_date": report_date.isoformat(),
        "sbp_reserves": sbp_reserves,
        "bank_reserves": bank_reserves,
        "total_reserves": total_reserves,
    }


def load_history() -> dict:
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="merged events JSON file")
    ap.add_argument("--out", default=None, help="defaults to overwriting the input file")
    ap.add_argument("--debug", action="store_true",
                     help="fetch and print the current snapshot only, no events file needed or touched")
    args = ap.parse_args()

    if not args.debug and not args.input:
        print("ERROR: input file required unless using --debug", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    snapshot = fetch_current_snapshot(session)

    if args.debug:
        if snapshot is None:
            print("Could not extract a snapshot -- either the date pattern or one of the "
                  "three number labels (SBP's Reserves / Bank's Reserves / Total Reserves) "
                  "wasn't found on the homepage. Page structure may have changed from what "
                  "this was built against.", file=sys.stderr)
        else:
            print(f"Report date:     {snapshot['report_date']}", file=sys.stderr)
            print(f"SBP's Reserves:  {snapshot['sbp_reserves']}", file=sys.stderr)
            print(f"Bank's Reserves: {snapshot['bank_reserves']}", file=sys.stderr)
            print(f"Total Reserves:  {snapshot['total_reserves']}", file=sys.stderr)
            print("\nCross-check these against sbp.org.pk directly, or a recent news "
                  "report, before trusting this for real dates.", file=sys.stderr)
        return

    with open(args.input) as f:
        events = json.load(f)

    if snapshot is None:
        print("WARNING: could not extract a current FX reserves snapshot from the "
              "homepage -- page structure may have changed. No events updated.",
              file=sys.stderr)
        with open(args.out or args.input, "w") as f:
            json.dump(events, f, indent=2)
        return

    print(f"INFO: current snapshot -- {snapshot}", file=sys.stderr)

    history = load_history()
    prior_value = None
    # look for the most recent PRIOR week already in our own history
    prior_dates = sorted((d for d in history if d < snapshot["report_date"]), reverse=True)
    if prior_dates:
        prior_value = history[prior_dates[0]]["sbp_reserves"]
        print(f"INFO: found prior week in our own history: {prior_dates[0]} "
              f"-> {prior_value}", file=sys.stderr)
    else:
        print("INFO: no prior week in history yet -- this is either the first run, "
              "or last week's confirm didn't happen. prior_value stays null this "
              "time, will self-populate from the next run onward.", file=sys.stderr)

    history[snapshot["report_date"]] = snapshot
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

    # find the predicted event closest to this report date and confirm it
    report_date = date.fromisoformat(snapshot["report_date"])
    best_match = None
    best_diff = None
    for e in events:
        if e.get("type") != "fx_reserves" or e.get("status") != "estimated":
            continue
        event_date = date.fromisoformat(e["date"])
        diff = abs((event_date - report_date).days)
        if diff <= 3 and (best_diff is None or diff < best_diff):
            best_match = e
            best_diff = diff

    if best_match is None:
        print("WARNING: no matching estimated fx_reserves event found within 3 days "
              "of the real report date -- was ingest_sbp_fx_reserves.py run for this "
              "date range?", file=sys.stderr)
    else:
        best_match["status"] = "confirmed"
        best_match["date"] = snapshot["report_date"]
        best_match["source_url"] = "https://www.sbp.org.pk/"
        best_match["payload"]["actual_value"] = snapshot["sbp_reserves"]
        best_match["payload"]["prior_value"] = prior_value
        best_match["payload"]["bank_reserves"] = snapshot["bank_reserves"]
        best_match["payload"]["total_reserves"] = snapshot["total_reserves"]
        print(f"INFO: confirmed event for {snapshot['report_date']}", file=sys.stderr)

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)
    print(f"\nDONE: written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
