"""
Ingests PSX's official 2026 market holiday calendar into normalized events.
Transcribed directly from PSX's own public notice (PSX/N-89, 20-Jan-2026),
fetched from https://dps.psx.com.pk/download/attachment/268947-1.pdf --
same "authoritative table transcription" pattern as ingest_sbp_mpc.py, not
a scrape or a computed rule.

IMPORTANT LIMITATION: PSX has not published a 2027 calendar yet. They
announce each year's holidays in mid-January of that same year (this 2026
notice went out 20-Jan-2026) -- so this script only covers Jan-Dec 2026.
Re-run/extend once PSX posts the 2027 notice, expected ~January 2027.

Two status tiers, matching PSX's own notice:
  - Fixed civil holidays (Kashmir Day, Pakistan Day, Labour Day,
    Youm-e-Takbeer, Independence Day, Iqbal Day, Quaid-e-Azam Day) --
    status "confirmed", same Gregorian date every year, not moon-dependent.
  - Islamic lunar holidays (Juma-tul-Wida, Eid-ul-Fitr, Eid-ul-Azha, Ashura,
    Eid Milad-un-Nabi) -- status "estimated", explicitly marked "*Subject
    to appearance of Moon" in PSX's own notice. These can shift by a day
    either direction based on the actual moon sighting announcement, even
    though PSX has pre-published a working date.

Run: python3 ingest_psx_holidays.py > events_holidays.json
"""
import json
import sys

SOURCE_URL = "https://dps.psx.com.pk/download/attachment/268947-1.pdf"

# (date, name, status) -- one row per calendar day, multi-day occasions
# (Eid-ul-Fitr, Eid-ul-Azha, Ashura) expanded into individual dates
HOLIDAYS_2026 = [
    ("2026-02-05", "Kashmir Day", "confirmed"),
    ("2026-03-20", "Juma-tul-Wida (last Friday of Ramadan)", "estimated"),
    ("2026-03-21", "Eid-ul-Fitr (day 1)", "estimated"),
    ("2026-03-22", "Eid-ul-Fitr (day 2)", "estimated"),
    ("2026-03-23", "Pakistan Day", "confirmed"),  # coincides with Eid-ul-Fitr day 3 this year
    ("2026-05-01", "Labour Day", "confirmed"),
    ("2026-05-28", "Youm-e-Takbeer", "confirmed"),
    ("2026-05-27", "Eid-ul-Azha (day 1)", "estimated"),
    ("2026-05-28", "Eid-ul-Azha (day 2)", "estimated"),  # coincides with Youm-e-Takbeer
    ("2026-05-29", "Eid-ul-Azha (day 3)", "estimated"),
    ("2026-06-24", "Ashura (day 1)", "estimated"),
    ("2026-06-25", "Ashura (day 2)", "estimated"),
    ("2026-08-14", "Independence Day", "confirmed"),
    ("2026-08-25", "Eid Milad-un-Nabi", "estimated"),
    ("2026-11-09", "Allama Iqbal Day", "confirmed"),
    ("2026-12-25", "Quaid-e-Azam Day / Christmas", "confirmed"),
]


def build_events():
    events = []
    seen_dates = set()  # PSX's own list has genuine same-day overlaps (see notes above)

    for date_str, name, status in HOLIDAYS_2026:
        event_id = f"psx-holiday-{date_str}-{name.split(' ')[0].lower()}"
        events.append({
            "id": event_id,
            "type": "market_holiday",
            "status": status,
            "date": date_str,
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": f"PSX closed \u2014 {name}",
            "source_url": SOURCE_URL,
            "payload": {
                "occasion": name,
                "moon_dependent": status == "estimated",
                "same_day_as_another_holiday": date_str in seen_dates,
            },
            "revision_of": None,
        })
        seen_dates.add(date_str)

    events.sort(key=lambda e: e["date"])
    return events


def main():
    events = build_events()
    json.dump(events, sys.stdout, indent=2)
    print(f"\n\n-- {len(events)} holiday events generated for 2026 "
          f"(2027 not yet published by PSX) --", file=sys.stderr)


if __name__ == "__main__":
    main()
