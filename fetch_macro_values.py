"""
Fetches macro values straight from SBP and PBS into macro_values.json, so the
calendar no longer needs FinHisaab for them (FinHisaab, refreshed weekly, is
now only the fallback; attach_finhisaab_values.py reads both files and the
direct values win).

Sources (static files only: SBP's HTML pages sit behind a Cloudflare
challenge that blocks GitHub Actions, but its PDFs and Excel files don't):

    remittances      SBP BPM6 summary workbook, "Workers' Remittances" row
    current_account  same workbook, "Current Account Balance" row
                     https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.xls
                     (holds the latest two months; history accumulates in
                     macro_values.json, which travels on gh-pages between runs)
    cpi_yoy          PBS CPI press release PDF for each released month
                     https://www.pbs.gov.pk/wp-content/uploads/2020/07/Press-Release-<Month>-<YYYY>.pdf
    policy_rate      SBP Monetary Policy Statement PDF for each past MPC
                     meeting on the calendar (the rate stated in its first
                     paragraph, whether held, cut or raised)

Each value is stored once per period with its source and URL. A fetch that
fails leaves what was already in the file, so a broken run never wipes
values. Months already in the file are not fetched again, except that the
BPM6 workbook is read every run because SBP revises the previous month.

Run (after merge_events.py, which supplies the MPC and CPI dates):
    python fetch_macro_values.py events_merged.json --out macro_values.json
"""
import argparse
import calendar
import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
}
BPM6_URL = "https://www.sbp.org.pk/ecodata/Balancepayment_BPM6.xls"
PBS_DIR = "https://www.pbs.gov.pk/wp-content/uploads/2020/07/"
SBP = "https://www.sbp.org.pk/"

LOOKBACK_DAYS = 400  # how far back to fill CPI months and MPC decisions

SERIES_META = {
    "remittances": {"unit": "USD million", "label": "Workers' remittances (SBP BPM6 summary)"},
    "current_account": {"unit": "USD million", "label": "Current account balance (SBP BPM6 summary)"},
    "cpi_yoy": {"unit": "percent", "label": "National CPI YoY (PBS press release)"},
    "policy_rate": {"unit": "percent", "label": "SBP policy rate (Monetary Policy Statement)"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
    except requests.RequestException as e:
        print(f"WARNING: {url}: {e}", file=sys.stderr)
        return None
    return r


def is_pdf(r: requests.Response | None) -> bool:
    return r is not None and r.status_code == 200 and r.content[:4] == b"%PDF"


def pdf_text(content: bytes, pages: int = 2) -> str:
    """Text of the first pages, or "" when the PDF can't be read."""
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as p:
            return "\n".join((pg.extract_text() or "") for pg in p.pages[:pages])
    except Exception as e:
        print(f"WARNING: unreadable PDF: {e}", file=sys.stderr)
        return ""


def month_end(y: int, m: int) -> str:
    return f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


# ---------- SBP BPM6 workbook: remittances + current account ----------

MONTHS = {calendar.month_abbr[i]: i for i in range(1, 13)}


def parse_bpm6(content: bytes) -> dict[str, dict[str, float]]:
    """{'remittances': {'2026-09-30': 3588.0, ...}, 'current_account': {...}}
    from the single-month columns of the BPM6 summary sheet."""
    import xlrd
    sh = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
    rows = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]

    head = next(i for i, r in enumerate(rows) if any(str(v).strip() == "Items" for v in r))
    months, fys = rows[head], rows[head + 1]
    cols = {}  # column -> period
    for c, (mon, fy) in enumerate(zip(months, fys)):
        mon, fy = str(mon).strip(), str(fy).strip()
        m = re.fullmatch(r"FY(\d{2})[A-Z]?", fy)
        if mon not in MONTHS or not m:
            continue
        mi, fy_end = MONTHS[mon], 2000 + int(m.group(1))
        cols[c] = month_end(fy_end - 1 if mi >= 7 else fy_end, mi)
    if not cols:
        raise ValueError("no single-month columns found in the BPM6 header")

    def label(r):
        return " ".join(str(r[0] or r[1]).split()).lower()

    found = {}
    for r in rows[head + 2:]:
        lab = label(r)
        if "current_account" not in found and lab.startswith("current account balance") \
                and "without" not in lab:
            found["current_account"] = r
        if "remittances" not in found and lab.startswith("workers") and "remit" in lab:
            found["remittances"] = r
    missing = {"current_account", "remittances"} - set(found)
    if missing:
        raise ValueError(f"rows not found in BPM6 sheet: {sorted(missing)}")

    out = {}
    for key, r in found.items():
        out[key] = {}
        for c, period in cols.items():
            v = r[c]
            if isinstance(v, (int, float)):
                out[key][period] = round(float(v), 1)
    return out


# ---------- PBS CPI press release ----------

CPI_YOY_RE = re.compile(r"\(\s*Year\s+on\s+Year\s*\)\s*(-?\d+(?:\.\d+)?)", re.I)


def cpi_urls(y: int, m: int) -> list[str]:
    full, abbr = calendar.month_name[m], calendar.month_abbr[m]
    names = [f"Press-Release-{full}-{y}.pdf", f"Press-Release-{full}{y}.pdf",
             f"Press-Release-{abbr}-{y}.pdf", f"CPI-Press-Release-{full}-{y}.pdf"]
    return [PBS_DIR + n for n in names]


def fetch_cpi(y: int, m: int) -> tuple[float, str] | None:
    for url in cpi_urls(y, m):
        r = get(url)
        if not is_pdf(r):
            continue
        text = pdf_text(r.content, 1)
        # the release must be for the month we asked for
        if calendar.month_name[m].lower() not in text.lower() or str(y) not in text:
            continue
        mm = CPI_YOY_RE.search(text)
        if mm:
            return float(mm.group(1)), url
    return None


# ---------- SBP Monetary Policy Statement ----------

# "keep the policy rate unchanged at 11.5 percent", "cut the policy rate by
# 100 bps to 10.5 percent", "by 1.5 percentage points to 10 percent": the
# rate is the number after the last "at"/"to" before "percent" (decimals in
# between are allowed, sentence ends are not).
RATE_RE = re.compile(
    r"policy\s+rate\b(?:[^.]|\.(?=\d)){0,160}?\b(?:at|to)\s+(\d{1,2}(?:\.\d{1,2})?)\s*(?:percent|%)",
    re.I | re.S)


def mps_urls(d: date) -> list[str]:
    full, abbr = calendar.month_name[d.month], calendar.month_abbr[d.month]
    variants = [full, abbr] + (["Sept"] if d.month == 9 else [])
    urls = []
    for v in variants:
        name = f"MPS-{v}-{d.year}-Eng.pdf"
        urls += [f"{SBP}assets/document/publications/{name}",
                 f"{SBP}m_policy/{d.year}/{name}"]
    return urls


def fetch_policy_rate(meeting: date) -> tuple[float, str] | None:
    for url in mps_urls(meeting):
        r = get(url)
        if not is_pdf(r):
            continue
        text = pdf_text(r.content, 1)
        # same meeting: the statement is dated the meeting day
        stamp = f"{calendar.month_name[meeting.month]} {meeting.day}, {meeting.year}"
        if stamp.lower() not in " ".join(text.split()).lower():
            print(f"INFO: {url} is not the {meeting} statement", file=sys.stderr)
            continue
        mm = RATE_RE.search(text)
        if mm:
            return float(mm.group(1)), url
    return None


# ---------- main ----------

def load(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("series", {})
    for k, meta in SERIES_META.items():
        s = d["series"].setdefault(k, {})
        s.update(unit=meta["unit"], label=meta["label"])
        s.setdefault("observations", [])
    return d


def put(series: dict, period: str, value: float, source: str, url: str) -> bool:
    """Insert or update one observation; returns True when something changed."""
    obs = series["observations"]
    for o in obs:
        if o["period"] == period:
            if o["value"] == value and o.get("url") == url:
                return False
            o.update(value=value, source=source, url=url)
            return True
    obs.append({"period": period, "value": value, "source": source, "url": url})
    obs.sort(key=lambda o: o["period"])
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events", help="events_merged.json (MPC and CPI dates)")
    ap.add_argument("--out", default="macro_values.json")
    args = ap.parse_args()

    data = load(args.out)
    s = data["series"]
    with open(args.events) as f:
        events = json.load(f)
    today = date.today()
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    changed, failures = 0, []

    # remittances + current account: every run (SBP revises last month)
    r = get(BPM6_URL)
    if r is not None and r.status_code == 200 and not r.content.lstrip().startswith(b"<"):
        try:
            parsed = parse_bpm6(r.content)
            for key, vals in parsed.items():
                for period, v in vals.items():
                    changed += put(s[key], period, v, "sbp", BPM6_URL)
                s[key]["last_sync"] = now_iso()
            print(f"INFO: BPM6 {parsed}", file=sys.stderr)
        except Exception as e:  # layout change: keep the old values, report it
            failures.append(f"BPM6 workbook: {e}")
    else:
        failures.append(f"BPM6 workbook: HTTP {r.status_code if r is not None else 'error'}")

    # CPI: one release per month; the early-month release reports the month before
    have_cpi = {o["period"][:7] for o in s["cpi_yoy"]["observations"]}
    for e in events:
        if e.get("type") != "cpi_monthly" or not (since <= e["date"] <= today.isoformat()):
            continue
        rel = date.fromisoformat(e["date"])
        ref = rel.replace(day=1) - timedelta(days=1)
        ym = ref.strftime("%Y-%m")
        if ym in have_cpi:
            continue
        got = fetch_cpi(ref.year, ref.month)
        if got:
            changed += put(s["cpi_yoy"], ym, got[0], "pbs", got[1])
            have_cpi.add(ym)
            s["cpi_yoy"]["last_sync"] = now_iso()
        elif (today - rel).days > 3:
            failures.append(f"CPI {ym}: press release not found")
        time.sleep(0.5)

    # policy rate: one statement per past MPC meeting
    have_mpc = {o["period"] for o in s["policy_rate"]["observations"]}
    for e in events:
        if e.get("type") != "mpc" or not (since <= e["date"] <= today.isoformat()):
            continue
        if e["date"] in have_mpc:
            continue
        got = fetch_policy_rate(date.fromisoformat(e["date"]))
        if got:
            changed += put(s["policy_rate"], e["date"], got[0], "sbp", got[1])
            s["policy_rate"]["last_sync"] = now_iso()
        elif (today - date.fromisoformat(e["date"])).days > 1:
            failures.append(f"MPC {e['date']}: statement not found")
        time.sleep(0.5)

    data["_about"] = ("Macro values fetched directly from SBP and PBS by fetch_macro_values.py. "
                      "attach_finhisaab_values.py prefers these over data/finhisaab_values.json.")
    data["generated_at"] = now_iso()
    data["failures"] = failures
    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)

    for k in SERIES_META:
        obs = s[k]["observations"]
        last = obs[-1] if obs else None
        print(f"  {k:16s} {len(obs)} values, latest {last and (last['period'], last['value'])}",
              file=sys.stderr)
    print(f"DONE: {changed} values added or revised", file=sys.stderr)
    for f_ in failures:
        print(f"WARNING: {f_}", file=sys.stderr)
    # a missing source is not fatal: FinHisaab fills the gap and the next run retries


if __name__ == "__main__":
    main()
