"""
Parses PSX's official PSX-KMI All Share Islamic Index recomposition
notice -- a PDF published semi-annually by PSX's own Strategy, Products &
Data Science department, listing every company's Shariah compliance
status. Confirmed real, current source: real text-layer PDF (no OCR
needed), ~538 companies with an explicit "Final Shariah Status" column
(Compliant / Non-Compliant) as of the December 2025 review, effective
5-Jun-2026.

SCOPE DECISION: this only extracts symbol + final compliance status, not
the individual financial screening ratios (debt ratio, investment ratio,
etc.) the notice also publishes. Two reasons: (1) the calendar's Shariah
toggle only needs a yes/no per symbol, not the underlying ratios: (2) the
raw PDF-extracted text has real column-order artifacts from the PDF's
multi-column layout (e.g. one row's sequence number ends up printed mid-row
instead of at the start -- confirmed against real extracted text), and
narrowing the extraction target to just two fields sidesteps most of that
mess rather than fighting it column by column.

Document has several distinct sections with the same core shape (a ticker,
company name, and an explicit Compliant/Non-Compliant status) but slightly
different formatting: the main ~500-company ratio table, a block of banks/
insurers marked "NC by Nature" (institutionally excluded by business type,
not by financial ratios), a REIT sub-table, an ETF sub-table, and a final
short appendix of small-cap symbols with no ratio columns at all. The
parser below is deliberately format-agnostic: any line containing both a
ticker-like token and an explicit compliance signal is treated as a data
row, everything else (headers, ratio-column labels, footnotes) is skipped.

Also present in the same document, NOT parsed here: the "Incoming/Outgoing
companies" section near the top, which only lists what changed, not each
company's current status -- deliberately excluded to avoid contaminating
results with entries that don't carry an explicit status on the same line.

Only covers the broader PSX-KMI ALL SHARE index (compliance universe),
not the narrower 30-company KMI-30 (a liquidity-ranked subset of this same
list) -- for a "is this stock Shariah-compliant" filter, the broader list
is the right one to check against.
"""
import re

# Any explicit compliance status on a line -- checked in this order since
# "Non-Compliant" contains "Compliant" as a substring
STATUS_PATTERN = re.compile(r"\b(Non-Compliant|Compliant)\b")
NC_BY_NATURE_PATTERN = re.compile(r"NC\s+by\s+[Nn]ature", re.I)

# Candidate ticker: 2-11 uppercase letters/digits, must start with a letter
# (excludes bare numbers, which are row sequence numbers, not symbols)
TICKER_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]{1,10})\b")

# words that look like tickers (all-caps) but aren't -- exclude explicitly
NOT_A_TICKER = {"NC", "NA", "REITS", "ETFS", "NOTE", "N", "A"}


def parse_kmi_line(line: str) -> tuple[str, bool] | None:
    """Returns (symbol, is_compliant) if this line is a real data row,
    None if it's a header/footnote/section-label line to skip."""
    line = line.strip()
    if not line:
        return None

    is_nc_by_nature = bool(NC_BY_NATURE_PATTERN.search(line))
    status_matches = STATUS_PATTERN.findall(line)

    if not is_nc_by_nature and not status_matches:
        return None  # no compliance signal at all -- header/footnote line

    ticker_matches = [t for t in TICKER_PATTERN.findall(line) if t not in NOT_A_TICKER]
    if not ticker_matches:
        return None

    symbol = ticker_matches[0]

    if is_nc_by_nature:
        # "NC by Nature" rows (banks, insurers, etc.) are institutionally
        # excluded regardless of what the Objective-column status token
        # says -- treat these as definitively non-compliant rather than
        # relying on an earlier token in the line matching by coincidence
        return symbol, False

    final_status = status_matches[-1]  # last match = Final Shariah Status column
    return symbol, final_status == "Compliant"


def parse_kmi_notice(text: str) -> dict:
    """Returns {symbol: is_compliant} for every data row found."""
    results = {}
    skipped_header_like = 0
    for line in text.splitlines():
        parsed = parse_kmi_line(line)
        if parsed:
            symbol, compliant = parsed
            results[symbol] = compliant
        elif line.strip() and any(c.isupper() for c in line):
            skipped_header_like += 1
    return results


if __name__ == "__main__":
    with open("fixtures/kmi_notice_full.txt") as f:
        text = f.read()
    results = parse_kmi_notice(text)
    compliant = sum(1 for v in results.values() if v)
    print(f"Parsed {len(results)} symbols, {compliant} compliant, "
          f"{len(results) - compliant} non-compliant\n")
    for symbol, is_compliant in results.items():
        print(f"  {symbol:12s} {'Compliant' if is_compliant else 'Non-Compliant'}")
