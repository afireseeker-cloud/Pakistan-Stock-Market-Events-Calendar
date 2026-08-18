"""
Fetches SBP's Balance of Payments (BPM6) summary table -- a single,
always-current PDF (no date-guessing needed, unlike remittances/FX
reserves; this file gets updated in place every month) -- and extracts
the Current Account Balance for the latest available month.

CONFIRMED CORRECT against a real visual screenshot of the actual table
(columns: Jul-Jun | Jun | Jul-Sep | Oct-Dec | Jan-Mar | May | Jun |
Apr-Jun | Jul-Jun | Jul-Jun, where the last two highlighted columns are
full FISCAL-YEAR totals, not individual months). Position [6] = the "Jun
FY26P" column, position [5] = "May FY26R" -- verified these are exactly
right: -649 for June 2026, 500 for May 2026 (matching what a human
reading the real table sees).

REAL RISK STILL WORTH KNOWING ABOUT: this uses a fixed position index
(6 and 5), not a label-driven lookup, because the header text extracts
messily as plain text ("FY26RFY26PFY26P" running together with no clear
separator) and building a fully robust label parser wasn't achievable
without more real samples than the single one confirmed so far. If SBP
ever changes this table's shape (a rolling window shifts, a column gets
added or dropped), a fixed index could start silently pointing at the
wrong number instead of failing loudly -- it would just look plausible
and be wrong. That's why --debug exists as a required first check, and
it's worth re-running that check periodically, not just this once, if
the current-account figure it produces ever looks implausible against
real news coverage.

Run:
    python3 fetch_sbp_current_account.py events_merged.json --debug
    python3 fetch_sbp_current_account.py events_merged.json --out events_merged.json
"""
import argparse
import json
import re
import sys
from io import BytesIO

import pdfplumber
import requests

BOP_URL = "https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.pdf"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def parse_number(text: str) -> float | None:
    text = text.strip().replace(",", "")
    if not text:
        return None
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.lstrip("-").strip("()")
    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


def extract_current_account_row(text: str) -> list[float] | None:
    """Finds the 'Current Account Balance' row (not the 'without Official
    Transfers' variant right below it) and returns every number in it, in
    the order they appear."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Current Account Balance") and "without" not in stripped.lower():
            # everything after the label is the numbers, space-separated
            after_label = stripped[len("Current Account Balance"):].strip()
            tokens = after_label.split()
            values = [parse_number(t) for t in tokens]
            values = [v for v in values if v is not None]
            return values
    return None


def fetch_bop_table() -> str | None:
    resp = requests.get(BOP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    with pdfplumber.open(BytesIO(resp.content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?", help="merged events JSON file")
    ap.add_argument("--out", default=None, help="defaults to overwriting the input file")
    ap.add_argument("--debug", action="store_true",
                     help="print every extracted value with its position, don't touch events")
    args = ap.parse_args()

    text = fetch_bop_table()
    if text is None:
        print("ERROR: could not fetch or read the BOP table", file=sys.stderr)
        sys.exit(1)

    values = extract_current_account_row(text)
    if not values:
        print("ERROR: found the page but couldn't locate a 'Current Account Balance' "
              "row with parseable numbers -- table structure may have changed",
              file=sys.stderr)
        sys.exit(1)

    if args.debug:
        print(f"Extracted {len(values)} values from the Current Account Balance row, "
              f"in document order:", file=sys.stderr)
        labels = {6: "-> confirmed correct: latest single month (e.g. Jun FY26P)",
                  5: "-> confirmed correct: month before that (e.g. May FY26R)",
                  8: "(full fiscal-year total -- not a single month, never used)",
                  9: "(full fiscal-year total -- not a single month, never used)"}
        for i, v in enumerate(values):
            print(f"  [{i}] {v}  {labels.get(i, '')}", file=sys.stderr)
        print("\nPositions [6] and [5] were confirmed correct against a real visual "
              "check of the table on one real month -- but this is still a fixed "
              "position index, not a label-driven lookup, so re-check this output "
              "against a news report periodically rather than assuming it'll always "
              "hold.", file=sys.stderr)
        return

    if not args.input:
        print("ERROR: input file required unless using --debug", file=sys.stderr)
        sys.exit(1)

    # best-guess positions -- see docstring and --debug output above for
    # the reasoning and the caveat
    if len(values) < 7:
        print(f"WARNING: only found {len(values)} values, expected at least 7 -- "
              f"table shape may differ from what this script assumes. Not updating "
              f"any events.", file=sys.stderr)
        sys.exit(1)

    latest_value = values[6]
    prior_value = values[5]

    with open(args.input) as f:
        events = json.load(f)

    updated = 0
    for e in events:
        if e.get("type") == "current_account" and e.get("status") == "estimated":
            e["status"] = "confirmed"
            e["source_url"] = BOP_URL
            e["payload"]["actual_value"] = latest_value
            e["payload"]["prior_value"] = prior_value
            updated += 1
            break  # only confirm the most recent pending one

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)
    print(f"DONE: {updated} event(s) updated (actual={latest_value}, prior={prior_value}), "
          f"written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
