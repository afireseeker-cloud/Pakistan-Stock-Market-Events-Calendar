"""
Ingests the SBP Monetary Policy Committee advance calendar into normalized
calendar events. Source is published once a year (or on revision) as a table
on SBP's site / picked up by financial press (e.g. Mettis Global, Business
Recorder, APP). There's no JSON feed for this -- it's a manually maintained
table, published maybe 2x/year. So this script takes the table as structured
input (hand-transcribed from the published release) rather than scraping,
since scraping a once-a-year press release table isn't worth automating yet.

Each MPC date fans out into up to 5 sub-events per the sbp deliverable model:
  - mpc            : the policy statement itself (always)
  - mpc_briefing   : analyst briefing slide deck, published next day
  - mpc_presser    : governor press conference (only 4 of 8 meetings/year)
  - mpc_minutes    : minutes released by end of a stated week
  - mpc_report     : biannual Monetary Policy Report (only 2 of 8 meetings/year)

Run: python3 ingest_sbp_mpc.py > events.json
"""
import json
import sys
from datetime import date

# FY27 advance calendar, as published 23-Jun-2026.
# source: SBP press release, reported by Mettis Global / Business Recorder / APP
# fields: (mpc_date, briefing_date, has_presser, minutes_by, mpr_date_or_None)
FY27_MPC_CALENDAR = [
    ("2026-07-27", "2026-07-28", True,  "2026-08-21", "2026-08-10"),
    ("2026-09-14", "2026-09-15", False, "2026-10-09", None),
    ("2026-10-26", "2026-10-27", True,  "2026-11-20", None),
    ("2026-12-14", "2026-12-15", False, "2027-01-08", None),
    ("2027-01-25", "2027-01-26", True,  "2027-02-19", "2027-02-08"),
    ("2027-03-08", "2027-03-09", False, "2027-04-02", None),
    ("2027-04-26", "2027-04-27", True,  "2027-05-21", None),
    ("2027-06-17", "2027-06-18", False, "2027-07-12", None),
]

SOURCE_URL = "https://mettisglobal.news/SBP-releases-FY27-MPC-meeting-calendar-expands-monetary-policy-communication-61352"


def build_events():
    events = []
    for mpc_date, briefing_date, has_presser, minutes_by, mpr_date in FY27_MPC_CALENDAR:
        base_id = f"sbp-mpc-{mpc_date}"

        events.append({
            "id": base_id,
            "type": "mpc",
            "status": "confirmed",
            "date": mpc_date,
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": "State Bank monetary policy statement (Monetary Policy Committee)",
            "source_url": SOURCE_URL,
            "payload": {"deliverable": "statement"},
            "revision_of": None,
        })

        events.append({
            "id": f"{base_id}-briefing",
            "type": "mpc_briefing",
            "status": "confirmed",
            "date": briefing_date,
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": "Monetary policy analyst briefing slide deck published",
            "source_url": SOURCE_URL,
            "payload": {"deliverable": "briefing", "related_mpc": mpc_date},
            "revision_of": None,
        })

        if has_presser:
            events.append({
                "id": f"{base_id}-presser",
                "type": "mpc_presser",
                "status": "confirmed",
                "date": mpc_date,
                "time": None,
                "scope": "market",
                "symbol": None,
                "sector": None,
                "title": "SBP governor post-policy press conference",
                "source_url": SOURCE_URL,
                "payload": {"deliverable": "presser", "related_mpc": mpc_date},
                "revision_of": None,
            })

        events.append({
            "id": f"{base_id}-minutes",
            "type": "mpc_minutes",
            "status": "confirmed",
            "date": minutes_by,
            "time": None,
            "scope": "market",
            "symbol": None,
            "sector": None,
            "title": "Monetary policy meeting minutes published (by week ending)",
            "source_url": SOURCE_URL,
            "payload": {"deliverable": "minutes", "related_mpc": mpc_date},
            "revision_of": None,
        })

        if mpr_date:
            events.append({
                "id": f"{base_id}-mpr",
                "type": "mpc_report",
                "status": "confirmed",
                "date": mpr_date,
                "time": None,
                "scope": "market",
                "symbol": None,
                "sector": None,
                "title": "Monetary Policy Report published",
                "source_url": SOURCE_URL,
                "payload": {"deliverable": "mpr", "related_mpc": mpc_date},
                "revision_of": None,
            })

    return events


def main():
    events = build_events()
    events.sort(key=lambda e: (e["date"], e["type"]))
    json.dump(events, sys.stdout, indent=2)
    print(f"\n\n-- {len(events)} events generated from {len(FY27_MPC_CALENDAR)} MPC meetings --",
          file=sys.stderr)


if __name__ == "__main__":
    main()
