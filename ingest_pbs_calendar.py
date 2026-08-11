"""
Generates PBS (Pakistan Bureau of Statistics) price-release calendar events.

Unlike SBP's MPC calendar, PBS does NOT publish an advance table of exact
future release dates -- SPI follows a fixed weekly rhythm (every Friday) and
CPI/WPI follow a fixed monthly rhythm (first working day or two of the
month, historically), but the precise date is only confirmed when PBS
actually publishes that period's bulletin (each bulletin page states "the
publication date of the next news bulletin on this subject is <date>").

So this generator produces events with status="estimated" for future dates
and "confirmed" for dates that have already passed (PBS's own cadence is
reliable enough to assume it happened once the date is behind us) --
distinct from the SBP script, which transcribes an already-confirmed table
regardless of date. This is the intended use of the status field from
the schema: a date PBS is very likely to hit, but hasn't itself committed to
in writing yet. Swap status to "confirmed" (and correct the date if it
shifted) once PBS's own "next bulletin" note is scraped for that period --
that's a v2 addition, not built here.

Run: python3 ingest_pbs_calendar.py --from 2026-08-01 --to 2026-12-31
"""
import argparse
import json
import sys
from datetime import date, timedelta

SOURCE_URL = "https://www.pbs.gov.pk/price-statistics/"


def fridays_between(start: date, end: date) -> list[date]:
    d = start
    while d.weekday() != 4:  # 4 = Friday
        d += timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def month_starts_between(start: date, end: date) -> list[date]:
    """First day of each month touched by the range -- monthly CPI/WPI
    releases land in the first few working days of the month, so we anchor
    the estimate to the 2nd of the month (skips New Year's Day collisions,
    close enough for a v1 estimate)."""
    out = []
    d = date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    return out


def build_events(start: date, end: date) -> list[dict]:
    events = []
    today = date.today()

    for friday in fridays_between(start, end):
        events.append({
            "id": f"pbs-spi-{friday.isoformat()}",
            "type": "spi_weekly",
            "status": "confirmed" if friday <= today else "estimated",
            "date": friday.isoformat(),
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": "Weekly Sensitive Price Indicator (SPI) release",
            "source_url": SOURCE_URL,
            "payload": {"recurrence": "weekly_friday"},
            "revision_of": None,
        })

    for month_start in month_starts_between(start, end):
        release_estimate = month_start + timedelta(days=1)  # ~2nd of month
        if start <= release_estimate <= end:
            for kind, label in (("cpi_monthly", "Monthly Consumer Price Index (CPI) release"),
                                 ("wpi_monthly", "Monthly Wholesale Price Index (WPI) release")):
                events.append({
                    "id": f"pbs-{kind}-{release_estimate.isoformat()}",
                    "type": kind,
                    "status": "confirmed" if release_estimate <= today else "estimated",
                    "date": release_estimate.isoformat(),
                    "time": None,
                    "scope": "market",
                    "symbol": None,
                    "sector": None,
                    "title": label,
                    "source_url": SOURCE_URL,
                    "payload": {"recurrence": "monthly_early",
                                "reference_month": month_start.strftime("%Y-%m")},
                    "revision_of": None,
                })

    events.sort(key=lambda e: (e["date"], e["type"]))
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
    print(f"\n\n-- {len(events)} estimated events generated "
          f"({start} to {end}) --", file=sys.stderr)


if __name__ == "__main__":
    main()
