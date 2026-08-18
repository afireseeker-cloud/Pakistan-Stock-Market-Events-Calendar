"""
Runs the entire PSX calendar pipeline in one command, in a strict
sequential order that avoids the exact bug we hit running things by hand:
extract_meeting_dates.py and classify_sentiment.py both load-modify-write
events_merged.json, and running them concurrently means whichever finishes
last silently overwrites the other's work. This script never runs two
steps that touch the same file at the same time -- everything is
sequential subprocess calls, each one waited on before the next starts.

Every enrichment step already caches by event id and skips already-
resolved items, so re-running this daily is naturally incremental: the
first run is slow (everything is new), every run after is fast (only
genuinely new events cost API calls or scrape time). This script doesn't
change that property, just makes sure it's exercised in the right order.

Steps requiring an API key (GEMINI_API_KEY, LOGO_DEV_TOKEN) are skipped
automatically with a clear message if that key isn't set -- this script
runs fine on a machine that's only set up one or two of the optional keys,
not all of them.

Shariah data is deliberately NOT auto-fetched here: the KMI compliance
notice's URL changes unpredictably every time PSX republishes it
(semi-annually), so there's nothing to auto-discover -- re-run
ingest_kmi_list.py by hand with the new URL when a new notice comes out
(check PSX's site every 6 months or so). attach_shariah.py DOES run every
time, reapplying whatever's already cached, since that's always safe.

Run:
    python run_pipeline.py                  # everything, including Gemini steps
    python run_pipeline.py --quick           # skip meeting-date OCR and sentiment (fast, no API wait)
    python run_pipeline.py --skip-vision     # meeting dates: text-extraction only, no Gemini
    python run_pipeline.py --skip-sentiment
    python run_pipeline.py --skip-logos
    python run_pipeline.py --skip-eps
    python run_pipeline.py --skip-shariah
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import date, timedelta

FETCH_LOOKBACK_DAYS = 90
FORWARD_WINDOW_DAYS = 365

MERGED_FILE = "events_merged.json"


def run_step(label: str, cmd: list[str], required_env: str | None = None,
             continue_on_error: bool = True, stdout_file: str | None = None) -> str:
    """Runs one subprocess step, returns 'ok', 'skip', or 'fail'. If
    required_env is set and that environment variable is missing, skips
    the step entirely rather than failing -- lets this script run on a
    machine that's only configured some of the optional API keys.
    stdout_file redirects the subprocess's stdout to a file, for the four
    ingest_*.py scripts that print JSON to stdout rather than taking a
    --out flag."""
    if required_env and not os.environ.get(required_env):
        print(f"SKIP  {label} -- {required_env} not set", file=sys.stderr)
        return "skip"

    print(f"\n{'=' * 60}\nRUNNING: {label}\n{'=' * 60}", file=sys.stderr)
    start = time.time()
    if stdout_file:
        with open(stdout_file, "w") as f:
            result = subprocess.run(cmd, stdout=f)
    else:
        result = subprocess.run(cmd)
    elapsed = time.time() - start

    if result.returncode == 0:
        print(f"OK    {label} ({elapsed:.0f}s)", file=sys.stderr)
        return "ok"

    print(f"FAIL  {label} (exit {result.returncode}, {elapsed:.0f}s)", file=sys.stderr)
    if not continue_on_error:
        print("Stopping pipeline -- this step was required for later steps.", file=sys.stderr)
        sys.exit(1)
    return "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                     help="skip both Gemini-based steps (meeting-date OCR, sentiment)")
    ap.add_argument("--skip-vision", action="store_true")
    ap.add_argument("--skip-sentiment", action="store_true")
    ap.add_argument("--skip-logos", action="store_true")
    ap.add_argument("--skip-eps", action="store_true")
    ap.add_argument("--skip-shariah", action="store_true")
    ap.add_argument("--skip-remittances", action="store_true",
                     help="skip the confirm step only -- predicted dates still generate")
    ap.add_argument("--skip-fx-reserves", action="store_true",
                     help="skip the confirm step only -- predicted dates still generate")
    ap.add_argument("--skip-current-account", action="store_true",
                     help="skip the confirm step only -- predicted dates still generate")
    args = ap.parse_args()

    skip_vision = args.quick or args.skip_vision
    skip_sentiment = args.quick or args.skip_sentiment

    today = date.today()
    fetch_from = (today - timedelta(days=FETCH_LOOKBACK_DAYS)).isoformat()
    fetch_to = today.isoformat()
    window_from = fetch_from
    window_to = (today + timedelta(days=FORWARD_WINDOW_DAYS)).isoformat()

    results = {}
    pipeline_start = time.time()

    # ---------- Tier 0: independent fetches ----------
    results["sbp"] = run_step(
        "SBP MPC calendar",
        [sys.executable, "ingest_sbp_mpc.py"],
        stdout_file="events_sbp.json",
    )
    results["pbs"] = run_step(
        "PBS calendar",
        [sys.executable, "ingest_pbs_calendar.py", "--from", window_from, "--to", window_to],
        stdout_file="events_pbs.json",
    )
    results["pama"] = run_step(
        "PAMA calendar",
        [sys.executable, "ingest_pama.py", "--from", window_from, "--to", window_to],
        stdout_file="events_pama.json",
    )
    results["holidays"] = run_step(
        "PSX holidays",
        [sys.executable, "ingest_psx_holidays.py"],
        stdout_file="events_holidays.json",
    )
    results["remittances_predict"] = run_step(
        "SBP remittances calendar (predicted dates)",
        [sys.executable, "ingest_sbp_remittances.py", "--from", window_from, "--to", window_to],
        stdout_file="events_remittances.json",
    )
    results["fx_reserves_predict"] = run_step(
        "SBP FX reserves calendar (predicted dates)",
        [sys.executable, "ingest_sbp_fx_reserves.py", "--from", window_from, "--to", window_to],
        stdout_file="events_fx_reserves.json",
    )
    results["current_account_predict"] = run_step(
        "SBP current account calendar (predicted dates)",
        [sys.executable, "ingest_sbp_current_account.py", "--from", window_from, "--to", window_to],
        stdout_file="events_current_account.json",
    )

    results["announcements"] = run_step(
        "PSX announcements",
        [sys.executable, "fetch_psx_announcements.py", "--from", fetch_from, "--to", fetch_to,
         "--out", "events_psx_announcements.jsonl"],
    )
    results["notices"] = run_step(
        "PSX notices",
        [sys.executable, "fetch_psx_announcements.py", "--from", fetch_from, "--to", fetch_to,
         "--type", "E", "--out", "events_psx_notices.jsonl"],
    )
    results["payouts"] = run_step(
        "PSX payouts",
        [sys.executable, "fetch_psx_payouts.py", "--out", "events_psx_payouts.jsonl"],
    )

    # ---------- Tier 1: merge ----------
    merge_ok = run_step(
        "Merge all sources",
        [sys.executable, "merge_events.py",
         "--sbp", "events_sbp.json", "--pbs", "events_pbs.json",
         "--pama", "events_pama.json", "--holidays", "events_holidays.json",
         "--remittances", "events_remittances.json",
         "--fx-reserves", "events_fx_reserves.json",
         "--current-account", "events_current_account.json",
         "--announcements", "events_psx_announcements.jsonl",
         "--notices", "events_psx_notices.jsonl",
         "--payouts", "events_psx_payouts.jsonl",
         "--out", MERGED_FILE],
        continue_on_error=False,  # nothing downstream makes sense without this
    )
    results["merge"] = merge_ok

    # ---------- Tier 2: sequential, both touch events_merged.json ----------
    if not skip_vision:
        results["meeting_dates"] = run_step(
            "Meeting date extraction (text + vision OCR)",
            [sys.executable, "extract_meeting_dates.py", MERGED_FILE, "--out", MERGED_FILE],
            required_env="GEMINI_API_KEY",
        )
    else:
        print("SKIP  Meeting date extraction (--quick/--skip-vision)", file=sys.stderr)
        results["meeting_dates"] = "skip"

    if not skip_sentiment:
        results["sentiment"] = run_step(
            "Sentiment classification",
            [sys.executable, "classify_sentiment.py", MERGED_FILE, "--out", MERGED_FILE],
            required_env="GEMINI_API_KEY",
        )
    else:
        print("SKIP  Sentiment classification (--quick/--skip-sentiment)", file=sys.stderr)
        results["sentiment"] = "skip"

    # ---------- Tier 2/3: cache builders + attach, disjoint fields, order-independent ----------
    if not args.skip_logos:
        run_step("Fetch company domains",
                  [sys.executable, "fetch_company_domains.py", "--from-events", MERGED_FILE])
        results["logos"] = run_step(
            "Attach company logos",
            [sys.executable, "attach_logos.py", MERGED_FILE, "--out", MERGED_FILE],
            required_env="LOGO_DEV_TOKEN",
        )
    else:
        print("SKIP  Company logos (--skip-logos)", file=sys.stderr)
        results["logos"] = "skip"

    if not args.skip_eps:
        run_step("Fetch EPS data",
                  [sys.executable, "fetch_eps_data.py", "--from-events", MERGED_FILE])
        results["eps"] = run_step(
            "Attach EPS data",
            [sys.executable, "attach_eps.py", MERGED_FILE, "--out", MERGED_FILE],
        )
    else:
        print("SKIP  EPS data (--skip-eps)", file=sys.stderr)
        results["eps"] = "skip"

    if not args.skip_shariah:
        if os.path.exists("kmi_compliance_cache.json"):
            results["shariah"] = run_step(
                "Attach Shariah compliance",
                [sys.executable, "attach_shariah.py", MERGED_FILE, "--out", MERGED_FILE],
            )
        else:
            print("SKIP  Shariah compliance -- kmi_compliance_cache.json not found. "
                  "Run ingest_kmi_list.py by hand with the current notice URL first "
                  "(this doesn't auto-update, the notice URL changes unpredictably).",
                  file=sys.stderr)
            results["shariah"] = "skip"
    else:
        print("SKIP  Shariah compliance (--skip-shariah)", file=sys.stderr)
        results["shariah"] = "skip"

    if not args.skip_remittances:
        results["remittances_confirm"] = run_step(
            "Confirm SBP remittances (real values)",
            [sys.executable, "fetch_sbp_remittances.py", MERGED_FILE, "--out", MERGED_FILE],
        )
    else:
        print("SKIP  Remittances confirm (--skip-remittances)", file=sys.stderr)
        results["remittances_confirm"] = "skip"

    if not args.skip_fx_reserves:
        results["fx_reserves_confirm"] = run_step(
            "Confirm SBP FX reserves (real values)",
            [sys.executable, "fetch_sbp_fx_reserves.py", MERGED_FILE, "--out", MERGED_FILE],
        )
    else:
        print("SKIP  FX reserves confirm (--skip-fx-reserves)", file=sys.stderr)
        results["fx_reserves_confirm"] = "skip"

    if not args.skip_current_account:
        results["current_account_confirm"] = run_step(
            "Confirm SBP current account (real values)",
            [sys.executable, "fetch_sbp_current_account.py", MERGED_FILE, "--out", MERGED_FILE],
        )
    else:
        print("SKIP  Current account confirm (--skip-current-account)", file=sys.stderr)
        results["current_account_confirm"] = "skip"

    # ---------- summary ----------
    total_elapsed = time.time() - pipeline_start
    print(f"\n{'=' * 60}\nPIPELINE SUMMARY ({total_elapsed / 60:.1f} min total)\n{'=' * 60}",
          file=sys.stderr)
    labels = {"ok": "OK  ", "skip": "SKIP", "fail": "FAIL"}
    for step, status in results.items():
        print(f"  {labels[status]}  {step}", file=sys.stderr)

    if any(status == "fail" for status in results.values()):
        print("\nOne or more OPTIONAL enrichment steps failed -- see FAIL lines above. "
              "This is not fatal: merge_events.py already succeeded by the time we "
              "reach this point (it's the one step that hard-stops the whole script on "
              "failure, via continue_on_error=False above), so there's a genuinely good "
              "events_merged.json ready to publish even if some enrichment didn't land "
              "this run. Exiting 0 deliberately, so the workflow's deploy step still "
              "runs -- a partial-but-real update beats no update at all. Fix the FAIL'd "
              "steps for next time, but don't let them block what already worked.",
              file=sys.stderr)
        # deliberately NOT sys.exit(1) here anymore -- see comment above.
        # Confirmed in production this was blocking deploy entirely: a run
        # with 11 successful steps and 2 failed optional ones still exited
        # non-zero, and since the GitHub Actions workflow has no
        # continue-on-error on this step, "Prepare publish directory" and
        # "Deploy to gh-pages" were both silently skipped -- meaning
        # genuinely good, freshly-scraped data never got published because
        # two unrelated enrichment steps had a bug.


if __name__ == "__main__":
    main()
