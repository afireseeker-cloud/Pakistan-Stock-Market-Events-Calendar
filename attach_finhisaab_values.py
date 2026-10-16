"""
Fills the actual/prior values on macro events from data/finhisaab_values.json.

That file is refreshed daily by a scheduled Claude task that reads FinHisaab
(SBP EasyData + PBS CPI) through its MCP connector and commits the result to
main. FinHisaab has no public API, so the GitHub Actions run can not call it
directly; it only reads the committed file.

FinHisaab is the source of truth for these event types, so its values
overwrite whatever an older scraper put there:

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


def by_month(series: dict) -> dict[str, float]:
    return {month_key(o["period"]): o["value"] for o in series.get("observations", [])}


def sync_date(series: dict) -> date | None:
    ts = series.get("last_sync")
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()


def set_values(e: dict, actual: float, prior: float | None, fmt, unit: str,
               period: str, url: str) -> None:
    p = e.setdefault("payload", {})
    p["actual_value"] = fmt(actual)
    p["prior_value"] = fmt(prior) if prior is not None else None
    p["actual_numeric"] = actual
    p["prior_numeric"] = prior
    p["unit"] = unit
    p["value_period"] = period
    p["value_source"] = "finhisaab"
    e["status"] = "confirmed"
    e["source_url"] = url


def apply_monthly(e: dict, months: dict[str, float], ym: str, unit: str) -> bool:
    if ym not in months:
        return False
    set_values(e, months[ym], months.get(prev_month(ym)), fmt_usd_mn, unit, ym, SOURCE_URL)
    return True


def apply_mpc(e: dict, series: dict, today: date) -> bool:
    meeting = date.fromisoformat(e["date"])
    synced = sync_date(series)
    # don't claim "held" until FinHisaab has synced after the meeting --
    # otherwise a cut announced today would show as unchanged
    if meeting > today or synced is None or synced <= meeting:
        return False

    obs = sorted(
        ((date.fromisoformat(o["period"]), o["value"]) for o in series["observations"]),
        key=lambda x: x[0],
    )
    before = [v for d, v in obs if d < meeting]
    if not before:
        return False
    prior = before[-1]
    changed = [v for d, v in obs
               if meeting <= d <= meeting + timedelta(days=MPC_EFFECTIVE_WINDOW_DAYS)]
    actual = changed[-1] if changed else prior

    set_values(e, actual, prior, fmt_pct, "percent", e["date"], SOURCE_URL)
    p = e["payload"]
    p["decision"] = "hold" if actual == prior else ("cut" if actual < prior else "hike")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", required=True)
    ap.add_argument("--values", default=VALUES_FILE)
    args = ap.parse_args()

    try:
        with open(args.values) as f:
            values = json.load(f)
    except FileNotFoundError:
        print(f"SKIP  {args.values} not found -- no FinHisaab values to attach", file=sys.stderr)
        return

    with open(args.events) as f:
        events = json.load(f)

    s = values["series"]
    remit = by_month(s["remittances"])
    ca = by_month(s["current_account"])
    cpi = by_month(s["cpi_yoy"])
    today = date.today()

    counts = {"remittances": [0, 0], "current_account": [0, 0], "mpc": [0, 0], "cpi_monthly": [0, 0]}
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
            ok = apply_mpc(e, s["policy_rate"], today)
        else:
            ok = apply_cpi(e, cpi)
        counts[t][0] += ok

    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)

    print(f"FinHisaab values generated {values.get('generated_at')}", file=sys.stderr)
    for t, (filled, due) in counts.items():
        print(f"  {t:16s} {filled}/{due} past events filled", file=sys.stderr)


if __name__ == "__main__":
    main()
