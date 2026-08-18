"""
Generates ESTIMATED calendar events for upcoming SBP current account /
Balance of Payments releases.

Weaker cadence evidence than remittances or FX reserves -- only one real
confirmed data point: SBP's own homepage previewed "Summary of Balance of
Payments BPM6 for July 2026, due Not Later Than August 18 2026" (~18 days
after month-end). Using that single point as the estimate; refine this if
you observe the actual pattern differs once fetch_sbp_current_account.py
has confirmed a few real months.

Run:
    python3 ingest_sbp_current_account.py --from 2026-08-01 --to 2027-08-01 > events_current_account.json
"""
import argparse
import json
import sys
from datetime import date


def month_add(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def generate_estimates(date_from: date, date_to: date) -> list[dict]:
    events = []
    cursor = date(date_from.year, date_from.month, 1)

    while cursor <= date_to:
        reporting_month = cursor
        release_month = month_add(reporting_month, 1)
        release_date = date(release_month.year, release_month.month, 18)

        if date_from <= release_date <= date_to:
            period_label = reporting_month.strftime("%B %Y")
            events.append({
                "id": f"sbp-ca-{reporting_month.strftime('%Y-%m')}",
                "type": "current_account",
                "status": "estimated",
                "date": release_date.isoformat(),
                "time": None,
                "scope": "market",
                "symbol": None,
                "sector": None,
                "title": f"Current Account Balance for {period_label}",
                "source_url": None,
                "payload": {
                    "period": period_label,
                    "actual_value": None,
                    "prior_value": None,
                },
                "revision_of": None,
            })
        cursor = month_add(cursor, 1)

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
    print(f"INFO: generated {len(events)} current account events", file=sys.stderr)


if __name__ == "__main__":
    main()
