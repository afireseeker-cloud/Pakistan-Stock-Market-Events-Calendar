"""
Adds a logo_url to each event whose symbol has a resolved website domain
in company_domains_cache.json, using logo.dev (the confirmed-current
successor to the now-fully-shut-down Clearbit Logo API).

Requires a free logo.dev publishable key -- sign up at logo.dev, no credit
card needed, and set it as LOGO_DEV_TOKEN. The API call itself costs
nothing on the free tier (500K requests/month), this script just builds
the URL string, it doesn't call logo.dev at request time -- rendering
happens in the browser via a normal <img> tag, same as any other image.

The UI (timeline_v*.html, avatarFor()) already has a graceful onerror
fallback to the initials-avatar if a logo_url ever 404s, so this script
doesn't need to validate URLs itself -- just construct them from resolved
domains and let the browser handle misses.

Run:
    set LOGO_DEV_TOKEN=pk_your_key_here        (Windows)
    export LOGO_DEV_TOKEN=pk_your_key_here      (Mac/Linux)
    python3 attach_logos.py events_merged.json --out events_merged.json
"""
import argparse
import json
import os
import sys

CACHE_FILE = "company_domains_cache.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="merged events JSON file")
    ap.add_argument("--out", default=None, help="defaults to overwriting the input file")
    ap.add_argument("--size", type=int, default=64, help="logo pixel size, default 64")
    args = ap.parse_args()

    token = os.environ.get("LOGO_DEV_TOKEN")
    if not token:
        print("ERROR: LOGO_DEV_TOKEN environment variable not set. Sign up free at "
              "logo.dev to get a publishable key.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(CACHE_FILE) as f:
            domain_cache = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {CACHE_FILE} not found -- run fetch_company_domains.py first.",
              file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        events = json.load(f)

    attached = 0
    for e in events:
        symbol = e.get("symbol")
        if not symbol:
            continue
        entry = domain_cache.get(symbol)
        if entry and entry.get("resolved") and entry.get("domain"):
            e["logo_url"] = (f"https://img.logo.dev/{entry['domain']}"
                              f"?token={token}&size={args.size}")
            attached += 1

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)

    print(f"DONE: logo_url attached to {attached} events, written to {out_path}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
