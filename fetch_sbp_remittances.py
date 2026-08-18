"""
Finds and parses real, already-published SBP Workers' Remittances press
releases, extracting the actual dollar figure and deriving the prior-year
comparison from the stated growth percentage. This is the "confirm" half
of the predict-then-confirm pattern -- ingest_sbp_remittances.py generates
an estimated date, this script finds the real release and promotes it to
confirmed with real numbers.

Confirmed real phrasing, consistent across independent releases (checked
against two separate real months, not assumed):
    "Workers' remittances recorded an inflow of US$ 3.1 billion during
    August 2025. ... In terms of growth, remittances increased by 6.6
    percent on y/y basis."

Confirmed real archive URL pattern (multiple real examples found):
    https://www.sbp.org.pk/press/{year}/Pr-{DD}-{Mon}-{YYYY}.pdf
The exact day varies (observed range: 6th-10th of the release month), so
this tries each candidate day in that range until one resolves to a real
PDF -- a genuine heuristic, not confirmed against every possible month.
The first real production run against this script IS the real test of
that heuristic, same as every other "can't fully verify without running
it for real" situation in this project.

Run:
    python3 fetch_sbp_remittances.py --debug --month 2026-07    # verify one month, no events file needed
    python3 fetch_sbp_remittances.py events_merged.json --out events_merged.json
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from io import BytesIO

import pdfplumber
import requests

RATE_LIMIT_SECONDS = 0.5
MAX_RETRIES = 2
CANDIDATE_DAYS = range(5, 13)  # observed range was 6th-10th; padded a couple days either side for safety

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Confirmed against two independent real releases with identical phrasing
VALUE_PATTERN = re.compile(
    r"inflow of US\$\s*([\d,]+\.?\d*)\s*billion during (\w+)\s+(\d{4}).*?"
    r"remittances (increased|decreased) by ([\d.]+)\s*percent on y/y basis",
    re.I | re.S,
)


def try_fetch_release(session: requests.Session, release_date: date, verbose: bool = False):
    """Tries each candidate day for a given release month, returns the
    first real PDF's extracted text, or None if nothing in the range hit.
    Retries a transient-looking failure (5xx, timeout, connection error)
    once with a short backoff before moving to the next candidate day --
    a 522 (Cloudflare couldn't reach SBP's origin server in time) is
    often a brief blip, not a permanent "this URL doesn't exist"."""
    for day in CANDIDATE_DAYS:
        month_abbr = MONTH_ABBR[release_date.month - 1]
        url = f"https://www.sbp.org.pk/press/{release_date.year}/Pr-{day:02d}-{month_abbr}-{release_date.year}.pdf"

        for attempt in range(MAX_RETRIES):
            if verbose:
                print(f"  trying {url} (attempt {attempt + 1}/{MAX_RETRIES}) ...", file=sys.stderr)
            try:
                resp = session.get(url, headers=HEADERS, timeout=20)
                if resp.status_code == 200:
                    with pdfplumber.open(BytesIO(resp.content)) as pdf:
                        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    if "remittance" in text.lower():  # sanity check -- got A pdf, is it THE remittances one?
                        if verbose:
                            print(f"    -> HIT, real PDF found and contains 'remittance'", file=sys.stderr)
                        return text, url
                    elif verbose:
                        print(f"    -> got a PDF but it doesn't mention remittances, not the right one",
                              file=sys.stderr)
                    break  # got a real 200 response either way, no point retrying THIS day again

                if verbose:
                    print(f"    -> HTTP {resp.status_code}", file=sys.stderr)
                if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
                    wait = 3 * (attempt + 1)
                    if verbose:
                        print(f"    -> server error, might be transient -- retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                break  # non-5xx (e.g. 404) means this day genuinely doesn't have a release -- move on, don't retry

            except requests.RequestException as e:
                if verbose:
                    print(f"    -> failed: {e}", file=sys.stderr)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                break

        time.sleep(RATE_LIMIT_SECONDS)
    return None, None


def extract_values(text: str):
    m = VALUE_PATTERN.search(text)
    if not m:
        return None
    actual = float(m.group(1).replace(",", ""))
    direction = m.group(4).lower()
    growth_pct = float(m.group(5)) / 100
    prior = actual / (1 + growth_pct) if direction == "increased" else actual / (1 - growth_pct)
    return {"actual_value": round(actual, 3), "prior_value": round(prior, 3),
            "growth_pct": growth_pct * 100 * (1 if direction == "increased" else -1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="merged events JSON file")
    ap.add_argument("--out", default=None, help="defaults to overwriting the input file")
    ap.add_argument("--debug", action="store_true",
                     help="verify one specific month standalone, no events file needed or touched")
    ap.add_argument("--month", help="YYYY-MM, the reporting month to check with --debug "
                                     "(defaults to last month if omitted)")
    args = ap.parse_args()

    if args.debug:
        if args.month:
            year, month = map(int, args.month.split("-"))
            reporting_month = date(year, month, 1)
        else:
            today = date.today()
            reporting_month = date(today.year, today.month - 1, 1) if today.month > 1 \
                else date(today.year - 1, 12, 1)
        release_month = reporting_month.month % 12 + 1
        release_year = reporting_month.year + (1 if reporting_month.month == 12 else 0)
        release_date = date(release_year, release_month, 1)

        print(f"Checking for {reporting_month.strftime('%B %Y')}'s remittances release "
              f"(expected sometime in {release_date.strftime('%B %Y')}, days 5-12):",
              file=sys.stderr)
        session = requests.Session()
        text, url = try_fetch_release(session, release_date, verbose=True)
        if text is None:
            print(f"\nNo release found in the candidate day range for "
                  f"{reporting_month.strftime('%B %Y')} -- either not published yet, "
                  f"or genuinely outside the 5th-12th window this run.", file=sys.stderr)
            return
        values = extract_values(text)
        print(f"\nFound real release at: {url}", file=sys.stderr)
        if values:
            print(f"Extracted: actual=${values['actual_value']}bn, "
                  f"prior=${values['prior_value']}bn, "
                  f"growth={values['growth_pct']:+.1f}% y/y", file=sys.stderr)
        else:
            print("WARNING: found the PDF but couldn't match the expected phrasing "
                  "pattern -- the real text may have a different wording than the two "
                  "confirmed examples this regex was built from. Paste the PDF's actual "
                  "content for a fix.", file=sys.stderr)
        return

    if not args.input:
        print("ERROR: input file required unless using --debug", file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        events = json.load(f)

    pending = [e for e in events if e.get("type") == "remittances" and e.get("status") == "estimated"]
    print(f"INFO: {len(pending)} remittances events pending confirmation", file=sys.stderr)

    session = requests.Session()
    confirmed = 0

    for e in pending:
        predicted_date = date.fromisoformat(e["date"])
        text, real_url = try_fetch_release(session, predicted_date)
        if text is None:
            continue  # not published yet, or genuinely not found in the candidate range -- stays estimated

        values = extract_values(text)
        if values is None:
            print(f"WARNING: found a PDF for {e['payload']['period']} but couldn't parse the "
                  f"expected phrasing -- leaving as estimated rather than guessing", file=sys.stderr)
            continue

        e["status"] = "confirmed"
        e["source_url"] = real_url
        e["payload"]["actual_value"] = values["actual_value"]
        e["payload"]["prior_value"] = values["prior_value"]
        e["payload"]["growth_pct_yoy"] = values["growth_pct"]
        confirmed += 1
        print(f"INFO: confirmed {e['payload']['period']} -- ${values['actual_value']}bn "
              f"(prior ${values['prior_value']}bn, {values['growth_pct']:+.1f}% y/y)", file=sys.stderr)

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)

    print(f"\nDONE: {confirmed}/{len(pending)} remittances events confirmed, written to {out_path}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
