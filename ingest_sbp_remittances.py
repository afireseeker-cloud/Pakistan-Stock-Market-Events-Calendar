"""
Generates ESTIMATED calendar events for upcoming SBP Workers' Remittances
releases -- the predictive half of the "predict, then confirm with real
numbers" pattern, same shape as ingest_pama.py's approach.

Real observed release dates, confirmed via independent search results
(not assumed):
    Aug 2025 data -> released Sep 08, 2025
    Feb 2026 data -> released Mar 10, 2026
    Jun 2026 data -> released Jul 06/09, 2026
    Jul 2026 data -> released Aug 10, 2026

Consistent pattern: 6th-10th of the month following the reporting month.
No single fixed day (unlike PAMA's tighter Monday-in-10th-14th pattern),
so this predicts the 8th as a reasonable midpoint -- fetch_sbp_remittances.py
will find and confirm the REAL date once actually published, same as PBS/
PAMA's estimated-then-confirmed status transition.

Run:
    python3 ingest_sbp_remittances.py --from 2026-08-01 --to 2027-08-01 > events_remittances.json
"""
import argparse
import json
import sys
from datetime import date, timedelta


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
        release_date = date(release_month.year, release_month.month, 8)  # midpoint of observed 6th-10th range

        if date_from <= release_date <= date_to:
            period_label = reporting_month.strftime("%B %Y")
            # Always "estimated" here, even if the predicted date has
            # already passed -- unlike PBS/CPI's tight, reliable schedule,
            # remittances has real observed variance (6th-10th, not one
            # fixed day), so a passed date doesn't mean the release
            # actually landed on THIS predicted day specifically. Only
            # fetch_sbp_remittances.py (which finds the real release)
            # should ever promote this to "confirmed".
            events.append({
                "id": f"sbp-remit-{reporting_month.strftime('%Y-%m')}",
                "type": "remittances",
                "status": "estimated",
                "date": release_date.isoformat(),
                "time": None,
                "scope": "market",
                "symbol": None,
                "sector": None,
                "title": f"Workers' Remittances for {period_label}",
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
    print(f"INFO: generated {len(events)} remittances events", file=sys.stderr)


if __name__ == "__main__":
    main()
