"""
Attaches shariah_compliant: true/false to every event whose symbol appears
in kmi_compliance_cache.json (built by ingest_kmi_list.py).

This directly activates the timeline UI's Shariah toggle, which currently
auto-disables itself with "Not available yet -- no Shariah-compliance
data source connected" specifically because no event has ever carried this
field. Once this runs, the toggle detects the field's presence and enables
itself automatically -- no UI code change needed, that behavior was built
in from the start for exactly this moment.

Note: this is symbol-level data, so it applies uniformly to every event
for a given company (a board meeting and a dividend for the same company
get the same compliance flag, which is correct -- compliance is a company
property, not an event property).

Run:
    python3 attach_shariah.py events_merged.json --out events_merged.json
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
        with open("kmi_compliance_cache.json") as f:
            compliance_cache = json.load(f)
    except FileNotFoundError:
        print("ERROR: kmi_compliance_cache.json not found -- run ingest_kmi_list.py first.",
              file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        events = json.load(f)

    attached = 0
    unmatched_symbols = set()
    for e in events:
        symbol = e.get("symbol")
        if not symbol:
            continue
        if symbol in compliance_cache:
            e["shariah_compliant"] = compliance_cache[symbol]
            attached += 1
        else:
            unmatched_symbols.add(symbol)

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)

    print(f"DONE: shariah_compliant attached to {attached} events, written to {out_path}",
          file=sys.stderr)
    if unmatched_symbols:
        print(f"INFO: {len(unmatched_symbols)} symbols in your events had no match in "
              f"the compliance list (funds/ETFs/ ecomposites not covered by this notice, "
              f"or symbols that have since changed): {sorted(unmatched_symbols)[:15]}"
              f"{'...' if len(unmatched_symbols) > 15 else ''}", file=sys.stderr)


if __name__ == "__main__":
    main()
