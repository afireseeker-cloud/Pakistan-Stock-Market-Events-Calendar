"""
Fetches PSX's official PSX-KMI All Share Islamic Index recomposition
notice (a PDF, published semi-annually) and builds a symbol -> compliant
cache from it.

This notice's URL changes every time PSX republishes it (semi-annually,
tied to companies' Dec 31/Jun 30 accounts) -- there's no stable "latest"
URL to hardcode, so this takes the PDF URL as an argument rather than
assuming last session's URL is still current. Find the current one by
searching PSX's own site or news coverage for "PSX-KMI All Share Islamic
Index recomposition notice" -- the filename pattern looks like
PSX-KM-ALL-Recomposition-<month>-<year>-N-<number>.pdf under
www.psx.com.pk/psx/themes/psx/uploads/.

Confirmed real, current source as of this build: December 2025 review,
effective 5-Jun-2026, 538 companies, real text-layer PDF (no OCR needed).
Note PSX subsequently published a corrigendum to this same notice
(15-Jul-2026) -- check for one before trusting a given run's results as
final, and prefer the corrigendum's numbers if it revises any company's
status.

Run:
    python3 ingest_kmi_list.py "https://www.psx.com.pk/psx/themes/psx/uploads/PSX-KM-ALL-Recomposition-Dec-2025-N-658.pdf"
"""
import argparse
import json
import sys
from io import BytesIO

import pdfplumber
import requests

from parse_kmi_list import parse_kmi_notice

CACHE_FILE = "kmi_compliance_cache.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_url", help="URL of the current PSX-KMI All Share recomposition notice")
    args = ap.parse_args()

    print(f"INFO: fetching {args.pdf_url}...", file=sys.stderr)
    resp = requests.get(args.pdf_url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    with pdfplumber.open(BytesIO(resp.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    print(f"INFO: extracted {len(text)} characters from {len(pdf.pages)} pages",
          file=sys.stderr)

    results = parse_kmi_notice(text)
    compliant_count = sum(1 for v in results.values() if v)
    print(f"INFO: parsed {len(results)} symbols -- {compliant_count} compliant, "
          f"{len(results) - compliant_count} non-compliant", file=sys.stderr)

    if len(results) < 100:
        print("WARNING: fewer than 100 symbols parsed -- this notice usually covers "
              "500+ companies. Either the PDF structure differs from what this parser "
              "expects, or something else went wrong. Check the output before trusting "
              "it.", file=sys.stderr)

    with open(CACHE_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nDONE: written to {CACHE_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
