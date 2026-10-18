"""
Fills the actual/prior values on macro events from two files:

    macro_values.json          fetched straight from SBP and PBS every run by
                               fetch_macro_values.py (primary)
    data/finhisaab_values.json refreshed weekly by a scheduled Claude task that
                               reads FinHisaab through its MCP connector
                               (fallback for anything the direct fetch missed)

For the same period the direct value wins. These values overwrite whatever an
older scraper put on the event:

    remittances      sbp-remit-YYYY-MM   total inflow, USD million
    current_account  sbp-ca-YYYY-MM      BPM6 current account balance, USD million
    mpc              sbp-mpc-YYYY-MM-DD  policy rate after the decision vs before
    cpi_monthly      pbs-cpi_monthly-*   CPI YoY for the month before the release

Weekly FX reserves and weekly SPI are not in FinHisaab, so they keep their
existing sources.

Because the values file holds full history, this step re-applies every value
on every run. That matters: the Actions runner starts with no
events_merged.json, so anything a scraper confirmed on an earlier run was
being lost on the next one.

Run:
    python attach_finhisaab_values.py events_merged.json --out events_merged.json
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta

VALUES_FILE = "data/finhisaab_values.json"
DIRECT_FILE = "macro_values.json"
SOURCE_URL = "https://finhisaab.com/economic-data"
CPI_SOURCE_URL = "https://finhisaab.com/inflation"

# a rate change shows up in SBP EasyData dated the meeting day or the day after
MPC_EFFECTIVE_WINDOW_DAYS = 7


def month_key(iso: str) -> str:
    return iso[:7]


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def fmt_usd_mn(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}M"


def fmt_pct(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + "%"


def obs_map(fh: dict | None, direct: dict | None, key=lambda p: p) -> dict:
    """{period_key: (value, source, url)}: FinHisaab first, direct values on top."""
    out = {}
    for series, source, url in ((fh, "finhisaab", None), (direct, None, None)):
        for o in (series or {}).get("observations", []):
            out[key(o["period"])] = (o["value"], o.get("source") or source or "finhisaab",
                                     o.get("url") or url)
    return out


def by_month(fh: dict | None, direct: dict | None) -> dict:
    return obs_map(fh, direct, month_key)


def sync_date(series: dict | None) -> date | None:
    ts = (series or {}).get("last_sync")
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()


def set_values(e: dict, actual, prior, fmt, unit: str, period: str, default_url: str) -> None:
    """actual/prior are (value, source, url) tuples (prior may be None)."""
    p = e.setdefault("payload", {})
    p["actual_value"] = fmt(actual[0])
    p["prior_value"] = fmt(prior[0]) if prior is not None else None
    p["actual_numeric"] = actual[0]
    p["prior_numeric"] = prior[0] if prior is not None else None
    p["unit"] = unit
    p["value_period"] = period
    p["value_source"] = actual[1]
    e["status"] = "confirmed"
    e["source_url"] = actual[2] or default_url


def apply_monthly(e: dict, months: dict, ym: str, unit: str) -> bool:
    if ym not in months:
        return False
    set_values(e, months[ym], months.get(prev_month(ym)), fmt_usd_mn, unit, ym, SOURCE_URL)
    return True


def apply_mpc(e: dict, rates: dict, fh_series: dict | None, today: date) -> bool:
    """rates: {date: (value, source, url)} from both files. A statement fetched
    for the meeting (or a FinHisaab change dated within a week of it) gives the
    decision; with neither, FinHisaab having synced after the meeting with no
    change means the rate was held."""
    meeting = date.fromisoformat(e["date"])
    if meeting > today:
        return False
    obs = sorted(((date.fromisoformat(d), v) for d, v in rates.items()), key=lambda x: x[0])
    before = [v for d, v in obs if d < meeting]
    after = [v for d, v in obs
             if meeting <= d <= meeting + timedelta(days=MPC_EFFECTIVE_WINDOW_DAYS)]
    # the statement for this meeting beats a FinHisaab entry dated a day later
    after = [v for v in after if v[1] != "finhisaab"] or after
    if not before and not after:
        return False
    prior = before[-1] if before else None
    if after:
        actual = after[-1]
    else:
        synced = sync_date(fh_series)
        # don't claim "held" until FinHisaab has synced after the meeting --
        # otherwise a cut announced today would show as unchanged
        if prior is None or synced is None or synced <= meeting:
            return False
        actual = (prior[0], "finhisaab", None)

    set_values(e, actual, prior, fmt_pct, "percent", e["date"], SOURCE_URL)
    p = e["payload"]
    if prior is not None:
        p["decision"] = "hold" if actual[0] == prior[0] else ("cut" if actual[0] < prior[0] else "hike")
    return True


def apply_cpi(e: dict, months: dict[str, float]) -> bool:
    release = date.fromisoformat(e["date"])
    # the early-month release reports the previous month
    ym = (release.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    e.setdefault("payload", {})["reference_month"] = ym
    if ym not in months:
        return False
    set_values(e, months[ym], months.get(prev_month(ym)), fmt_pct, "percent YoY", ym,
               CPI_SOURCE_URL)
    return True


def load_values(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f).get("series", {}) or {}
    except FileNotFoundError:
        print(f"INFO: {path} not found", file=sys.stderr)
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"WARNING: {path} unreadable ({e}), ignoring it", file=sys.stderr)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", required=True)
    ap.add_argument("--values", default=VALUES_FILE, help="FinHisaab values (fallback)")
    ap.add_argument("--direct", default=DIRECT_FILE, help="SBP/PBS values (primary)")
    args = ap.parse_args()

    fh, direct = load_values(args.values), load_values(args.direct)
    if not fh and not direct:
        print("SKIP  no macro values to attach", file=sys.stderr)
        return

    with open(args.events) as f:
        events = json.load(f)

    remit = by_month(fh.get("remittances"), direct.get("remittances"))
    ca = by_month(fh.get("current_account"), direct.get("current_account"))
    cpi = by_month(fh.get("cpi_yoy"), direct.get("cpi_yoy"))
    rates = obs_map(fh.get("policy_rate"), direct.get("policy_rate"))
    today = date.today()

    counts = {"remittances": [0, 0], "current_account": [0, 0], "mpc": [0, 0], "cpi_monthly": [0, 0]}
    sources = {}
    for e in events:
        t = e.get("type")
        if t not in counts or e.get("date", "9999") > today.isoformat():
            continue
        counts[t][1] += 1
        if t == "remittances":
            ok = apply_monthly(e, remit, e["id"].removeprefix("sbp-remit-"), "USD million")
        elif t == "current_account":
            ok = apply_monthly(e, ca, e["id"].removeprefix("sbp-ca-"), "USD million")
        elif t == "mpc":
            ok = apply_mpc(e, rates, fh.get("policy_rate"), today)
        else:
            ok = apply_cpi(e, cpi)
        counts[t][0] += ok
        if ok:
            src = e["payload"]["value_source"]
            sources[(t, src)] = sources.get((t, src), 0) + 1

    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)

    for t, (filled, due) in counts.items():
        by = ", ".join(f"{n} from {src}" for (tt, src), n in sorted(sources.items()) if tt == t)
        print(f"  {t:16s} {filled}/{due} past events filled ({by or 'none'})", file=sys.stderr)


if __name__ == "__main__":
    main()
