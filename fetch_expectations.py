"""
Broker expectations for the next CPI release and the next MPC decision.

Pakistani brokerages (Topline Securities, Arif Habib, JS Global, AKD and
others) publish a CPI preview a few days before PBS releases the number,
and a policy-rate poll or call before each MPC meeting. Business Recorder,
Mettis Global and Profit usually report them. There is no structured feed,
so this asks Gemini with Google Search grounding to find the published
expectation and name the broker.

Strict on purpose -- a blank is better than a made-up number:
  * only for a CPI or MPC event in the next EXPECT_WINDOW_DAYS days
  * the answer must name at least one brokerage and carry a number in a
    plausible range, and Gemini's grounding must show web sources
  * otherwise nothing is stored and the row shows no expectation

Results are cached in expectations.json (kept on gh-pages between runs)
by event id, re-checked at most once a day, and copied onto the events as
payload.expected_value / expected_source.

Run (after attach_finhisaab_values.py):
    python fetch_expectations.py events_merged.json --out events_merged.json
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

CACHE_FILE = "expectations.json"
EXPECT_WINDOW_DAYS = 10
RECHECK_HOURS = 20
MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3-flash", "gemini-2.5-flash",
          "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
BROKERS = re.compile(
    r"topline|arif habib|\bahl\b|js global|\bakd\b|insight securities|optimus|"
    r"ktrade|k-trade|al meezan|tresmark|ismail iqbal|chase securities|aba ali habib|"
    r"intermarket|pearl securities|next capital|foundation securities|alpha capital|"
    r"sherman|elixir|bma capital|growth securities|ihs|taurus|alfalah securities|"
    r"ahl research|reuters poll|bloomberg survey|brokerage", re.I)

PROMPTS = {
    "cpi_monthly": (
        "What year-on-year CPI inflation do Pakistani brokerages expect for {month} "
        "(the figure the Pakistan Bureau of Statistics releases around {release})? "
        "Use only previews published by brokerages or reported in Pakistani financial news "
        "(e.g. Topline Securities, Arif Habib Limited, JS Global, AKD Securities). "
        "Answer with exactly one line in this form and nothing else:\n"
        "VALUE|BROKERS\n"
        "VALUE is the expected YoY CPI in percent as a number (e.g. 9.5; if a range, its midpoint), "
        "BROKERS names the brokerages whose estimate you used. If you can not find a published "
        "brokerage estimate for {month}, answer exactly NONE."),
    "mpc": (
        "What policy rate do Pakistani brokerages and analyst polls expect the State Bank of "
        "Pakistan's Monetary Policy Committee to set at its meeting on {release}? "
        "The current policy rate is {current}. Use only brokerage research or polls reported in "
        "Pakistani financial news (e.g. Topline Securities poll, Arif Habib Limited, JS Global, AKD). "
        "Answer with exactly one line in this form and nothing else:\n"
        "VALUE|BROKERS\n"
        "VALUE is the expected policy rate in percent as a number (e.g. 11 for a cut to 11%, or "
        "the current rate if a hold is expected), BROKERS names the sources. If you can not find "
        "a published expectation for this meeting, answer exactly NONE."),
}
RANGES = {"cpi_monthly": (-5.0, 60.0), "mpc": (3.0, 30.0)}


def ask(prompt: str, api_key: str) -> tuple[str | None, int]:
    """(answer text, number of grounding sources) or (None, 0)."""
    body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(url, headers={"x-goog-api-key": api_key}, json=body, timeout=90)
        except requests.RequestException as e:
            print(f"WARNING: {model}: {e}", file=sys.stderr)
            continue
        if r.status_code in (429, 404, 400, 403, 503):
            print(f"INFO: {model} HTTP {r.status_code}, trying the next model", file=sys.stderr)
            continue
        try:
            r.raise_for_status()
            cand = r.json()["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
            chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
            return text, len(chunks)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            print(f"WARNING: {model}: unexpected response ({e})", file=sys.stderr)
    return None, 0


def parse(text: str | None, kind: str) -> tuple[float, str] | None:
    if not text or text.strip().upper().startswith("NONE"):
        return None
    line = next((l for l in text.strip().splitlines() if "|" in l), None)
    if line is None:
        return None
    val, brokers = line.split("|", 1)
    brokers = " ".join(brokers.split())[:80]
    rng = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", val)
    m = re.search(r"-?\d+(?:\.\d+)?", val)
    if not m or not BROKERS.search(brokers):
        return None
    v = (float(rng.group(1)) + float(rng.group(2))) / 2 if rng else float(m.group(0))
    lo, hi = RANGES[kind]
    if not (lo <= v <= hi):
        return None
    return v, brokers


def fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + "%"


def current_rate(events: list[dict], before: str) -> str:
    past = sorted((e for e in events if e.get("type") == "mpc" and e.get("date", "") < before
                   and (e.get("payload") or {}).get("actual_value")), key=lambda e: e["date"])
    return past[-1]["payload"]["actual_value"] if past else "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.events) as f:
        events = json.load(f)
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}

    api_key = os.environ.get("GEMINI_API_KEY")
    today = date.today()
    until = (today + timedelta(days=EXPECT_WINDOW_DAYS)).isoformat()
    now = datetime.now(timezone.utc)

    targets = [e for e in events if e.get("type") in PROMPTS
               and today.isoformat() <= e.get("date", "") <= until]
    for e in targets:
        c = cache.get(e["id"], {})
        checked = c.get("checked_at")
        fresh = checked and now - datetime.fromisoformat(checked.replace("Z", "+00:00")) \
            < timedelta(hours=RECHECK_HOURS)
        if fresh or not api_key:
            continue
        release = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%d %B %Y")
        if e["type"] == "cpi_monthly":
            ref = date.fromisoformat(e["date"]).replace(day=1) - timedelta(days=1)
            prompt = PROMPTS["cpi_monthly"].format(month=ref.strftime("%B %Y"), release=release)
        else:
            prompt = PROMPTS["mpc"].format(release=release, current=current_rate(events, e["date"]))
        text, sources = ask(prompt, api_key)
        got = parse(text, e["type"]) if sources else None
        print(f"INFO: {e['id']}: {text!r} ({sources} sources) -> {got}", file=sys.stderr)
        entry = {"checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if got:
            entry.update(value=fmt(got[0]), source=got[1])
        elif c.get("value"):
            entry.update(value=c["value"], source=c.get("source"))  # keep an earlier find
        cache[e["id"]] = entry
        time.sleep(4.5)

    applied = 0
    for e in events:
        c = cache.get(e["id"])
        if c and c.get("value") and e.get("date", "") >= today.isoformat():
            p = e.setdefault("payload", {})
            p["expected_value"], p["expected_source"] = c["value"], c.get("source") or ""
            applied += 1

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)
    if not api_key:
        print("SKIP  GEMINI_API_KEY not set -- cached expectations applied only", file=sys.stderr)
    print(f"DONE: {len(targets)} CPI/MPC events in the next {EXPECT_WINDOW_DAYS} days, "
          f"{applied} carry a broker expectation", file=sys.stderr)


if __name__ == "__main__":
    main()
