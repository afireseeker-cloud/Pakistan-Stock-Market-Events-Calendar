"""
Attaches EPS data to "results" events from eps_data_cache.json, filling in
the payload.eps_actual/eps_prior fields the Earnings tab's dataColumns()
has been reading (and rendering as honest blanks) since it was first built.

IMPORTANT SIMPLIFICATION, stated plainly: this attaches a company's LATEST
KNOWN EPS figures to EVERY "results" event for that symbol, not the exact
figure that specific announcement covers. A results event dated six months
ago and one dated yesterday for the same company will show the same EPS
numbers -- whatever the most recent scrape found. Precisely matching each
announcement to its exact reporting period would mean parsing period
phrasing out of free-text titles ("Nine Months ended March 31, 2026" ->
which quarter is that for this company's fiscal year?) across however many
different real phrasings companies use -- a real, separate piece of work,
not built here. What's here is honestly scoped: "the company's current
known EPS," not "the exact EPS this specific announcement reported."

Prefers quarterly EPS (more timely) over annual when both exist.

Run:
    python3 attach_eps.py events_merged.json --out events_merged.json
"""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="merged events JSON file")
    ap.add_argument("--out", default=None, help="defaults to overwriting the input file")
    args = ap.parse_args()

    try:
        with open("eps_data_cache.json") as f:
            eps_cache = json.load(f)
    except FileNotFoundError:
        print("ERROR: eps_data_cache.json not found -- run fetch_eps_data.py first.",
              file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        events = json.load(f)

    attached = 0
    for e in events:
        if e.get("type") != "results":
            continue
        symbol = e.get("symbol")
        if not symbol:
            continue
        entry = eps_cache.get(symbol)
        if not entry or not entry.get("resolved"):
            continue

        eps_actual = entry.get("latest_quarterly_eps")
        eps_prior = entry.get("yoy_quarterly_eps")
        if eps_actual is None:
            eps_actual = entry.get("latest_annual_eps")
            eps_prior = entry.get("prior_annual_eps")

        if eps_actual is not None:
            e.setdefault("payload", {})["eps_actual"] = eps_actual
            e["payload"]["eps_prior"] = eps_prior
            e["payload"]["eps_period"] = entry.get("latest_quarterly_label")
            attached += 1

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)

    print(f"DONE: EPS data attached to {attached} results events, written to {out_path}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
