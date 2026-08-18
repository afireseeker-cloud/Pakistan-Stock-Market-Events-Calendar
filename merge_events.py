"""
Merges the four ingestion sources into a single event store:
  - events_sbp.json              (JSON array, from ingest_sbp_mpc.py)
  - events_pbs.json              (JSON array, from ingest_pbs_calendar.py)
  - events_psx_announcements.jsonl (JSON Lines, from fetch_psx_announcements.py)
  - events_psx_payouts.jsonl       (JSON Lines, from fetch_psx_payouts.py)

Two formats because the SBP/PBS generators emit small, complete arrays in
one shot, while the PSX fetchers stream large paginated results line-by-line
-- JSONL is the natural fit for the latter. This script normalizes both into
one JSON array on the way out.

Adds a `source` field (not present in any individual script's output) so
the merged store can trace every event back to which ingestion pipeline
produced it -- useful once this runs on a schedule and something needs
debugging.

Dedup: on `id`. Every script already builds IDs meant to be globally unique
(prefixed sbp-/pbs-/psx-annc-/psx-payout-), so cross-source collisions
shouldn't happen -- but SAME-source re-runs (e.g. re-fetching an overlapping
date range) will produce identical ids, which is exactly the case this
guards against.

Run:
    python3 merge_events.py \\
        --sbp events_sbp.json \\
        --pbs events_pbs.json \\
        --announcements events_psx_announcements.jsonl \\
        --payouts events_psx_payouts.jsonl \\
        --out events_merged.json
"""
import argparse
import json
import sys


def load_json_array(path: str, source: str) -> list[dict]:
    with open(path) as f:
        events = json.load(f)
    for e in events:
        e["source"] = source
    return events


def load_jsonl(path: str, source: str) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                e["source"] = source
                events.append(e)
    return events


def carry_forward_enrichment(new_events: list[dict], previous_output_path: str) -> int:
    """Re-running merge_events.py rebuilds the whole event list from the raw
    per-source files every time -- which means it previously had no memory
    of anything attach_logos.py, attach_eps.py, attach_shariah.py,
    classify_sentiment.py, or extract_meeting_dates.py had already written.
    Every re-merge silently discarded all of that enrichment. Confirmed
    this actually happened in production, not just a theoretical risk: a
    real merged file had 1916 events with Shariah data attached, then a
    later re-merge (to pick up fresh announcements) wiped it back to zero
    without anyone re-running attach_shariah.py to notice.

    This carries forward every enrichment field from the previous output
    file (if one exists) onto matching event ids in the freshly-rebuilt
    list, keyed by id. Critically, this includes overwriting the freshly-
    rebuilt event's date/time with the corrected values from
    extract_meeting_dates.py when present -- otherwise a re-merge would
    silently revert a real scheduled-meeting date back to the raw filing
    date, since the raw source always has the filing date, never the
    corrected one.

    Returns the number of events enrichment was carried forward onto."""
    try:
        with open(previous_output_path) as f:
            previous_events = json.load(f)
    except FileNotFoundError:
        return 0  # first-ever merge, nothing to carry forward

    previous_by_id = {e["id"]: e for e in previous_events}
    carried = 0

    for e in new_events:
        prev = previous_by_id.get(e["id"])
        if prev is None:
            continue

        touched = False
        for field in ("logo_url", "sentiment", "shariah_compliant"):
            if field in prev:
                e[field] = prev[field]
                touched = True

        prev_payload = prev.get("payload", {})
        if prev_payload.get("date_source"):
            # a real scheduled date was previously found -- restore it,
            # otherwise this re-merge would silently revert to the raw
            # filing date pulled fresh from the source file
            e["date"] = prev["date"]
            e["time"] = prev["time"]
            e.setdefault("payload", {})["date_source"] = prev_payload["date_source"]
            e["payload"]["filed_date"] = prev_payload.get("filed_date")
            e["payload"]["filed_time"] = prev_payload.get("filed_time")
            touched = True

        if prev_payload.get("eps_actual") is not None:
            e.setdefault("payload", {})["eps_actual"] = prev_payload["eps_actual"]
            e["payload"]["eps_prior"] = prev_payload.get("eps_prior")
            e["payload"]["eps_period"] = prev_payload.get("eps_period")
            touched = True

        if touched:
            carried += 1

    return carried


def merge(sources: dict[str, tuple[str, str]], date_from: str | None = None,
          date_to: str | None = None) -> list[dict]:
    """sources: {label: (path, format)} where format is 'json' or 'jsonl'.
    date_from/date_to (inclusive, YYYY-MM-DD) trim the final merged set to a
    consistent window regardless of what each source's own range happened to
    be -- important because the sources don't naturally align: SBP/PBS are
    generated for whatever range you pass their own scripts, while PSX
    payouts returns full history with no date param to scope it at the
    source at all. Trimming here, once, after merge, is the one place that
    can guarantee every source lines up to the same window."""
    all_events = []
    for label, (path, fmt) in sources.items():
        try:
            loader = load_json_array if fmt == "json" else load_jsonl
            events = loader(path, label)
            print(f"INFO: loaded {len(events)} events from {label} ({path})",
                  file=sys.stderr)
            all_events.extend(events)
        except FileNotFoundError:
            print(f"WARNING: {path} not found, skipping {label} -- "
                  f"run its ingestion script first if this is unexpected",
                  file=sys.stderr)

    seen_ids = set()
    deduped = []
    id_collisions = 0
    for e in all_events:
        if e["id"] in seen_ids:
            id_collisions += 1
            continue
        seen_ids.add(e["id"])
        deduped.append(e)

    if id_collisions:
        print(f"INFO: {id_collisions} duplicate ids dropped on merge",
              file=sys.stderr)

    before_trim = len(deduped)
    if date_from:
        deduped = [e for e in deduped if e["date"] >= date_from]
    if date_to:
        deduped = [e for e in deduped if e["date"] <= date_to]
    if (date_from or date_to) and len(deduped) != before_trim:
        print(f"INFO: {before_trim - len(deduped)} events trimmed outside "
              f"[{date_from or '-inf'}, {date_to or '+inf'}]", file=sys.stderr)

    # sort by date, then time (None sorts first -- undated/all-day events
    # lead each day), then type for stable ordering within a day
    deduped.sort(key=lambda e: (e["date"], e.get("time") or "", e["type"]))

    return deduped


def default_window() -> tuple[str, str]:
    """90 days back, 12 months forward from today -- a reasonable default
    so the merged calendar doesn't silently include years of stale corporate
    history alongside a thin macro calendar just because one source (PSX
    payouts) has no natural date bound of its own. Override with --from/--to."""
    from datetime import date, timedelta
    today = date.today()
    return (today - timedelta(days=90)).isoformat(), (today + timedelta(days=365)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sbp", default="events_sbp.json")
    ap.add_argument("--pbs", default="events_pbs.json")
    ap.add_argument("--pama", default="events_pama.json")
    ap.add_argument("--holidays", default="events_holidays.json")
    ap.add_argument("--remittances", default="events_remittances.json")
    ap.add_argument("--fx-reserves", dest="fx_reserves", default="events_fx_reserves.json")
    ap.add_argument("--current-account", dest="current_account", default="events_current_account.json")
    ap.add_argument("--announcements", default="events_psx_announcements.jsonl")
    ap.add_argument("--notices", default="events_psx_notices.jsonl")
    ap.add_argument("--payouts", default="events_psx_payouts.jsonl")
    ap.add_argument("--out", default="events_merged.json")
    ap.add_argument("--from", dest="date_from", default=None,
                     help="YYYY-MM-DD, defaults to 90 days ago")
    ap.add_argument("--to", dest="date_to", default=None,
                     help="YYYY-MM-DD, defaults to 12 months from today")
    ap.add_argument("--no-window", action="store_true",
                     help="disable date trimming entirely, keep every source's full native range")
    args = ap.parse_args()

    if args.no_window:
        date_from, date_to = None, None
    else:
        default_from, default_to = default_window()
        date_from = args.date_from or default_from
        date_to = args.date_to or default_to

    sources = {
        "sbp_mpc": (args.sbp, "json"),
        "pbs_calendar": (args.pbs, "json"),
        "pama_auto_sales": (args.pama, "json"),
        "psx_holidays": (args.holidays, "json"),
        "sbp_remittances": (args.remittances, "json"),
        "sbp_fx_reserves": (args.fx_reserves, "json"),
        "sbp_current_account": (args.current_account, "json"),
        "psx_announcements": (args.announcements, "jsonl"),
        "psx_notices": (args.notices, "jsonl"),
        "psx_payouts": (args.payouts, "jsonl"),
    }

    merged = merge(sources, date_from=date_from, date_to=date_to)

    carried = carry_forward_enrichment(merged, args.out)
    if carried:
        print(f"INFO: carried forward enrichment (logos/sentiment/EPS/Shariah/corrected "
              f"dates) onto {carried} events from the previous {args.out}", file=sys.stderr)

    with open(args.out, "w") as f:
        json.dump(merged, f, indent=2)

    by_source = {}
    for e in merged:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1

    print(f"\nDONE: {len(merged)} total events written to {args.out}",
          file=sys.stderr)
    print(f"  window: {date_from or 'unbounded'} to {date_to or 'unbounded'}",
          file=sys.stderr)
    for label, count in sorted(by_source.items()):
        print(f"  {label:20s} {count:5d}", file=sys.stderr)

    if merged:
        print(f"\nDate range: {merged[0]['date']} to {merged[-1]['date']}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
