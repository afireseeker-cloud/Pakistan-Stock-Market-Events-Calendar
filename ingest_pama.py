"""
Generates PAMA (Pakistan Automotive Manufacturers Association) monthly
production and sales data release events.

Like PBS, PAMA publishes no advance calendar of exact future release dates.
What's confirmed instead: PAMA releases month M's data on the Monday
between the 10th and 14th of month M+1 -- verified against a real
observation (data released 10-Aug-2026, which was a Monday). See
find_release_monday() for the edge case where that 5-day window doesn't
contain a Monday at all.

Run:
    python3 ingest_pama.py --from 2026-08-01 --to 2026-12-31
"""
import argparse
import json
import sys
from datetime import date, timedelta

SOURCE_URL = "https://pama.org.pk/monthly-production-sales-of-vehicles/"


def month_starts_between(start: date, end: date) -> list[date]:
    out = []
    d = date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return out


def find_release_monday(release_month: date) -> date:
    """PAMA releases the previous month's data on the Monday that falls
    between the 10th and 14th of the following month -- confirmed by a real
    observation (10-Aug-2026, a Monday). A 5-day window doesn't always
    contain a Monday (if day 10 itself is a Tuesday, the window 10-14 is
    Tue-Sat with no Monday at all) -- in that case this falls back to the
    nearest Monday on or after the 10th, which may land on the 15th or 16th.
    That's a reasonable interpretation of the rule, not a separate
    assumption -- flagged in payload either way."""
    d = date(release_month.year, release_month.month, 10)
    while d.weekday() != 0:  # 0 = Monday
        d += timedelta(days=1)
    return d


def build_events(start: date, end: date) -> list[dict]:
    events = []
    today = date.today()

    # Look back one extra reference month at the start boundary: month M's
    # release lands in month M+1, so if `start` falls in the middle of that
    # M+1 window, the naive month_starts_between(start, end) would skip M
    # entirely and miss a release date that's still >= start. Each
    # candidate gets checked against [start, end] below regardless, so this
    # only ever adds candidates, never produces anything out of range.
    lookback_month = date(start.year, start.month, 1)
    lookback_month = date(lookback_month.year - 1, 12, 1) if lookback_month.month == 1 \
        else date(lookback_month.year, lookback_month.month - 1, 1)

    for month_start in month_starts_between(lookback_month, end):
        # PAMA publishes month M's data in month M+1, on the Monday
        # between the 10th and 14th -- see find_release_monday().
        if month_start.month == 12:
            publish_month = date(month_start.year + 1, 1, 1)
        else:
            publish_month = date(month_start.year, month_start.month + 1, 1)
        release_estimate = find_release_monday(publish_month)
        within_window = 10 <= release_estimate.day <= 14

        if start <= release_estimate <= end:
            events.append({
                "id": f"pama-auto-sales-{release_estimate.isoformat()}",
                "type": "auto_sales_monthly",
                "status": "confirmed" if release_estimate <= today else "estimated",
                "date": release_estimate.isoformat(),
                "time": None,
                "scope": "sector",
                "symbol": None,
                "sector": "AUTOMOBILE",
                "title": "Monthly automobile production and sales data (PAMA)",
                "source_url": SOURCE_URL,
                "payload": {"recurrence": "monthly_following_monday_10_to_14",
                            "reference_month": month_start.strftime("%Y-%m"),
                            "date_confidence": "confirmed_pattern" if within_window
                                                else "fallback_monday_outside_10_14_window"},
                "revision_of": None,
            })

    events.sort(key=lambda e: e["date"])
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    args = ap.parse_args()

    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    events = build_events(start, end)

    json.dump(events, sys.stdout, indent=2)
    print(f"\n\n-- {len(events)} events generated ({start} to {end}) --",
          file=sys.stderr)


if __name__ == "__main__":
    main()
