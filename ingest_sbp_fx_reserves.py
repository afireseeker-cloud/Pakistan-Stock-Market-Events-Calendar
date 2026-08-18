"""
Generates ESTIMATED calendar events for upcoming SBP weekly FX reserves
releases. Same predict-then-confirm pattern as ingest_sbp_remittances.py.

Confirmed real cadence: weekly, Friday releases, reporting the week ending
that same Friday (or very close to it) -- consistent with multiple
independent news reports checked across 2025-2026 ("released on Friday",
repeated across several separate articles spanning different months).

Run:
    python3 ingest_sbp_fx_reserves.py --from 2026-08-01 --to 2027-08-01 > events_fx_reserves.json
"""
import argparse
import json
import sys
from datetime import date, timedelta


def fridays_between(date_from: date, date_to: date) -> list[date]:
    # first Friday on/after date_from
    days_until_friday = (4 - date_from.weekday()) % 7  # Monday=0 ... Friday=4
    cursor = date_from + timedelta(days=days_until_friday)
    out = []
    while cursor <= date_to:
        out.append(cursor)
        cursor += timedelta(days=7)
    return out


def generate_estimates(date_from: date, date_to: date) -> list[dict]:
    events = []
    for release_date in fridays_between(date_from, date_to):
        events.append({
            "id": f"sbp-fxres-{release_date.isoformat()}",
            "type": "fx_reserves",
            "status": "estimated",
            "date": release_date.isoformat(),
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": f"Weekly FX Reserves \u2014 week ending {release_date.strftime('%d %b %Y')}",
            "source_url": None,
            "payload": {
                "week_ending": release_date.isoformat(),
                "actual_value": None,
                "prior_value": None,
            },
            "revision_of": None,
        })
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    args = ap.parse_args()

    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    events = generate_estimates(date_from, date_to)

    print(json.dumps(events, indent=2))
    print(f"INFO: generated {len(events)} FX reserves events", file=sys.stderr)


if __name__ == "__main__":
    main()
