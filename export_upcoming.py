"""
Writes upcoming.json: a compact slice of events_merged.json for the
portfolio tracker's "Coming Up" list (the pkdata Worker reads it and picks
the rows for each user's holdings and watch list).

events_merged.json is several MB; this file keeps only what that list
needs, so the Worker can parse it within the free plan's CPU limit:

  company  board meetings, AGMs/EOGMs, corporate briefings and dividend
           book closures from today on, plus results filed in the last
           5 days (the tracker shows those as "Results out")
  macro    MPC, CPI, current account, remittances, weekly FX reserves,
           weekly SPI, PAMA auto sales and PSX holidays from today on,
           each with the last released value of its kind ("last") and a
           broker expectation when one was found ("exp")

Run (last pipeline step, after values are attached):
    python export_upcoming.py events_merged.json --out upcoming.json
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone

COMPANY_TYPES = {"board_meeting", "agm", "corporate_briefing", "results", "book_closure"}
MACRO_TYPES = {"mpc", "cpi_monthly", "current_account", "remittances", "fx_reserves",
               "spi_weekly", "auto_sales_monthly", "market_holiday"}
RECENT_DAYS = 5
MAX_TITLE = 90
RESULTS_TITLE = re.compile(r"financial results?\b|results? for the|un-?audited|audited accounts", re.I)


def fmt_fx(v) -> str:
    return f"${float(v) / 1000:,.2f}bn"


def actual_text(e: dict) -> str | None:
    p = e.get("payload") or {}
    v = p.get("actual_value")
    if v is None or v == "":
        return None
    if e["type"] == "fx_reserves":
        try:
            return fmt_fx(v)
        except (TypeError, ValueError):
            return None
    return str(v)


def period_label(e: dict) -> str:
    p = e.get("payload") or {}
    ym = p.get("value_period") or p.get("reference_month") or p.get("week_ending") or ""
    try:
        if len(ym) == 7:
            return datetime.strptime(ym, "%Y-%m").strftime("%b")
        if len(ym) == 10:
            return datetime.strptime(ym, "%Y-%m-%d").strftime("%d %b")
    except ValueError:
        pass
    return ""


MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def title_period_end(title: str) -> tuple[int, int] | None:
    """(year, month) of the period end a results title names, e.g. 'Quarter
    Ended 30-09-2026', '30 September 2026', '2026-09-30', 'June 30, 2026'."""
    t = title.lower()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b\d{1,2}[-/.](\d{1,2})[-/.](\d{4})\b", t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,-]+(?:\d{1,2}(?:st|nd|rd|th)?[\s,]+)?(\d{4})", t)
    if m:
        return int(m.group(2)), MONTHS[m.group(1)]
    m = re.search(r"\b\d{1,2}(?:st|nd|rd|th)?[\s-]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,-]+(\d{4})", t)
    if m:
        return int(m.group(2)), MONTHS[m.group(1)]
    return None


def eps_matches(title: str, eps_period: str | None) -> bool:
    end = title_period_end(title)
    m = re.fullmatch(r"Q([1-4]) (\d{4})", (eps_period or "").strip())
    if not end or not m:
        return False
    return int(m.group(2)) == end[0] and int(m.group(1)) == (end[1] - 1) // 3 + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", default="upcoming.json")
    args = ap.parse_args()

    with open(args.events) as f:
        events = json.load(f)
    today = date.today().isoformat()
    recent_from = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()

    # last released value per macro type, from events up to today
    last = {}
    for e in sorted(events, key=lambda e: e.get("date", "")):
        if e.get("type") in MACRO_TYPES and e.get("date", "9") <= today:
            txt = actual_text(e)
            if txt:
                last[e["type"]] = {"v": txt, "p": period_label(e), "d": e["date"]}

    out = []
    for e in events:
        t, d = e.get("type"), e.get("date", "")
        if e.get("status") == "cancelled" or not d:
            continue
        p = e.get("payload") or {}
        row = {"d": d, "ty": t}
        if e.get("time"):
            row["tm"] = e["time"]
        if t in COMPANY_TYPES and e.get("symbol"):
            if t == "results":
                # a results filing, not "Transmission of Annual Report" and the like
                if not (recent_from <= d <= today) or not RESULTS_TITLE.search(e.get("title") or "") \
                        or re.search(r"transmission", e.get("title") or "", re.I):
                    continue
                # EPS comes from the PSX company page, which can still show an
                # older quarter; keep it only when its quarter is the one the
                # filing's title names
                if p.get("eps_actual") is not None and eps_matches(e.get("title") or "", p.get("eps_period")):
                    row.update(eps=p["eps_actual"], per=p.get("eps_period"))
                    if p.get("eps_prior") is not None:
                        row["epsp"] = p["eps_prior"]
            elif t == "book_closure":
                if e.get("source") != "psx_payouts":
                    continue
                lb = p.get("estimated_last_buy_date")
                if (lb or d) < today:
                    continue
                div = (p.get("dividend") or {}).get("display") or ""
                row.update(div=div, lb=lb, bc=p.get("book_closure_start"), bce=p.get("book_closure_end"))
            else:
                if d < today:
                    continue
                if t == "board_meeting" and p.get("will_consider_results"):
                    row["res"] = 1
            row["sym"] = e["symbol"]
        elif t in MACRO_TYPES:
            if d < today:
                continue
            if t in last:
                row["last"] = last[t]
            if p.get("expected_value"):
                row["exp"] = {"v": p["expected_value"], "src": p.get("expected_source") or ""}
        else:
            continue
        row["ti"] = (e.get("title") or "")[:MAX_TITLE]
        if e.get("source_url"):
            row["url"] = e["source_url"]
        out.append(row)

    out.sort(key=lambda r: (r["d"], r.get("tm") or ""))
    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": today,
        "events": out,
    }
    with open(args.out, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    n_co = sum(1 for r in out if "sym" in r)
    print(f"DONE: {len(out)} events ({n_co} company, {len(out) - n_co} macro) written to {args.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
