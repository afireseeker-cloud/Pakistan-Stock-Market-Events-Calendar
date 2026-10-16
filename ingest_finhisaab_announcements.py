"""
Turns data/finhisaab_announcements.json into calendar events.

This replaces the direct PSX scrapers (fetch_psx_announcements.py,
fetch_psx_payouts.py), which stopped working because dps.psx.com.pk returns
403 to GitHub Actions. FinHisaab reads the same PSX announcements and
already extracts the parts this calendar needed Gemini for: the real
scheduled meeting date and time, sentiment, EPS and declared dividends.

Mapping:
    board meeting notice (meeting date known) -> board_meeting on the meeting date
    corporate briefing notice (date known)    -> corporate_briefing on the briefing date
    financial results                          -> results (EPS) on the filing date,
                                                  plus dividend_announced if one was declared
    AGM/EGM notice                             -> agm on the filing date
    insider transaction                        -> director_disclosure
    management change                          -> governance_change
    everything else                            -> other_announcement

Mutual-fund pseudo-symbols (e.g. MCBIM-FUNDS) are skipped: their daily
dividend distributions and fund statements would bury real company events.

Book closures (ex-date / last-buy date) are not available from FinHisaab, so
the Dividends tab stays empty until that has a source again.

Run:
    python ingest_finhisaab_announcements.py > events_finhisaab_company.json
"""
import json
import sys

IN_FILE = "data/finhisaab_announcements.json"
SOURCE = "finhisaab_announcements"

SENTIMENT = {"good": "positive", "bad": "negative", "neutral": "neutral"}
CANCELLED = {"CANCELLED", "CANCELED", "POSTPONED", "REVOKED"}
SIMPLE_TYPES = {
    "AGM_EGM": "agm",
    "INSIDER_TRANSACTION": "director_disclosure",
    "MANAGEMENT": "governance_change",
}


def day(iso: str | None) -> str | None:
    return iso[:10] if iso else None


def is_fund(item: dict) -> bool:
    return (item.get("symbol") or "").upper().endswith("-FUNDS")


def base_event(item: dict, ev_type: str, date: str, time: str | None, suffix: str = "") -> dict:
    status = "confirmed"
    if (item.get("eventStatus") or "").upper() in CANCELLED:
        status = "cancelled"
    bm = item.get("boardMeeting") or {}
    if (bm.get("meetingStatus") or "").upper() in CANCELLED:
        status = "cancelled"

    e = {
        "id": f"fh-{item['id']}{suffix}",
        "type": ev_type,
        "status": status,
        "date": date,
        "time": time,
        "scope": "company",
        "symbol": item.get("symbol"),
        "sector": item.get("sector"),
        "title": item.get("title"),
        "company_name": item.get("companyName"),
        "source_url": item.get("documentUrl") or item.get("imageUrl"),
        "payload": {
            "summary": item.get("summary"),
            "filed_date": day(item.get("announcementDate")),
            "document_url": item.get("documentUrl"),
            "image_url": item.get("imageUrl"),
            "finhisaab_id": item["id"],
            "news_type": item.get("newsType"),
            "impact": item.get("impact"),
        },
        "revision_of": None,
        "source": SOURCE,
    }
    s = SENTIMENT.get((item.get("sentiment") or "").lower())
    if s:
        e["sentiment"] = s
    if item.get("isShariahCompliant") is not None:
        e["shariah_compliant"] = item["isShariahCompliant"]
    return e


def dividend_info(div: dict) -> dict | None:
    if not div or not div.get("declared"):
        return None
    kind = (div.get("type") or "").title() or None
    pct, per_share = div.get("ratePercent"), div.get("perShareAmount")
    if pct is not None:
        display = f"{pct:g}% {kind or ''}".strip()
    elif per_share is not None:
        display = f"Rs {per_share:g}/share {kind or ''}".strip()
    else:
        display = kind or "Declared"
    return {"percent": pct, "kind": kind, "status": None, "display": display,
            "per_share": per_share}


def results_events(item: dict) -> list[dict]:
    fr = item.get("financialResults") or {}
    filed = day(item.get("announcementDate"))
    e = base_event(item, "results", filed, None)
    cur = (fr.get("quarterFigures") or {}).get("current") or (fr.get("periodFigures") or {}).get("current") or {}
    cmp_ = (fr.get("quarterFigures") or {}).get("comparative") or (fr.get("periodFigures") or {}).get("comparative") or {}
    if cur.get("eps") is not None:
        e["payload"]["eps_actual"] = cur["eps"]
        e["payload"]["eps_prior"] = cmp_.get("eps")
        e["payload"]["eps_period"] = fr.get("reportType")
        e["payload"]["eps_source"] = "finhisaab"
    if cur.get("profit") is not None:
        e["payload"]["profit"] = cur["profit"]
        e["payload"]["figures_unit"] = fr.get("figuresUnit")
    out = [e]

    div = dividend_info(fr.get("dividend"))
    if div:
        d = base_event(item, "dividend_announced", filed, None, suffix="-div")
        d["title"] = f"Dividend declared: {div['display']}"
        d["payload"]["dividend"] = div
        out.append(d)
    return out


def to_events(item: dict) -> list[dict]:
    if not item.get("id") or not item.get("symbol") or is_fund(item):
        return []

    bm = item.get("boardMeeting") or {}
    cb = item.get("corporateBriefing") or {}
    news = item.get("newsType")
    filed = day(item.get("announcementDate"))

    if bm.get("meetingDate"):
        e = base_event(item, "board_meeting", day(bm["meetingDate"]), bm.get("meetingTime"))
        e["payload"].update({
            "date_source": "finhisaab",
            "purpose": bm.get("purpose"),
            "will_consider_results": bm.get("willConsiderFinancialResults"),
            "will_consider_entitlement": bm.get("willConsiderEntitlement"),
            "financial_period_ended": day(bm.get("financialPeriodEnded")),
            "financial_period_type": bm.get("financialPeriodType"),
        })
        return [e]

    if cb.get("briefingDate") and item.get("briefingSubtype") in (None, "UPCOMING_NOTICE"):
        e = base_event(item, "corporate_briefing", day(cb["briefingDate"]), cb.get("briefingTime"))
        e["payload"].update({"date_source": "finhisaab", "is_virtual": cb.get("isVirtual")})
        return [e]

    if item.get("financialResults"):
        return results_events(item)

    if item.get("eventDate") and news == "AGM_EGM":
        return [base_event(item, "agm", day(item["eventDate"]), None)]

    if not filed:
        return []
    return [base_event(item, SIMPLE_TYPES.get(news, "other_announcement"), filed, None)]


def dedupe(events: list[dict]) -> list[dict]:
    """The same meeting is often filed twice (a PDF and an image of the same
    notice, or a revised notice). Keep one event per symbol/type/date for
    scheduled items, preferring the latest filing."""
    scheduled = {"board_meeting", "corporate_briefing", "agm"}
    best = {}
    others = []
    for e in events:
        if e["type"] not in scheduled:
            others.append(e)
            continue
        key = (e["symbol"], e["type"], e["date"])
        cur = best.get(key)
        if cur is None or (e["payload"]["filed_date"] or "") >= (cur["payload"]["filed_date"] or ""):
            best[key] = e
    return list(best.values()) + others


def main():
    try:
        with open(IN_FILE) as f:
            items = json.load(f)["items"]
    except FileNotFoundError:
        print(f"WARNING: {IN_FILE} not found -- no company events", file=sys.stderr)
        json.dump([], sys.stdout)
        return

    events = []
    for item in items.values():
        events.extend(to_events(item))
    events = dedupe(events)
    events.sort(key=lambda e: (e["date"], e.get("time") or "", e["type"]))

    json.dump(events, sys.stdout, indent=2, ensure_ascii=False)

    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"\nDONE: {len(events)} company events from {len(items)} FinHisaab announcements",
          file=sys.stderr)
    for t, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {t:22s} {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
