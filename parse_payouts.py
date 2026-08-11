"""
Parses the /payouts endpoint's HTML response into calendar events. Confirmed
via live DevTools capture (11-Aug-2026): POST to
    https://dps.psx.com.pk/payouts
    body: symbol=LUCK&count=25&offset=0

This table is structurally cleaner than /announcements -- purpose-built
columns instead of free-text titles needing regex classification:
    Symbol | Company | Sector | Dividend Announcement | Date/Time of
    Announcement | Book Closure Date

Two things this table gives that the general announcements feed doesn't:
  1. "Dividend Announcement" in PSX shorthand, e.g. "250%(F) (D)" --
     percent-of-face-value, status (F=Final/I=Interim), type (D=cash
     Dividend/B=Bonus/R=Right). Parsed into structured fields below.
  2. Book Closure Date as an explicit start-end RANGE, not a single date --
     this is the actual mechanism PSX uses; there's no separate "ex-date"
     field published anywhere on the site. Everyone (including PSX's own
     investors) computes the effective ex-date and last-buy-date themselves
     from this range.

*** Settlement-lag assumption: partially validated, not yet fully proven ***
The commonly-cited rule ("ex-date = book closure start minus 2 working
days") is from the T+2 settlement era. PSX/NCCPL moved to T+1 settlement on
9-Feb-2026. Under T+1 the required lead time should shrink by one working
day -- and a live check against LUCK's real August 2026 book closure
confirms this: computed estimated_ex_date was 2026-08-17, matching PSX's
actual published XD date exactly. That's one confirmed data point, not a
guarantee it holds for every company/instrument type -- keep validating
against real XD announcements as they come in, and treat this as a strong
working assumption rather than settled fact until confirmed across several
more companies.
"""
import json
import re
import sys
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

STATUS_LABELS = {"F": "final", "I": "interim"}
KIND_LABELS = {"D": "cash dividend", "B": "bonus", "R": "right"}
ROMAN_TO_ORDINAL = {
    "i": "1st", "ii": "2nd", "iii": "3rd", "iv": "4th", "v": "5th",
    "vi": "6th", "vii": "7th", "viii": "8th", "ix": "9th", "x": "10th",
}


def parse_dividend_shorthand(raw: str) -> dict:
    """Decodes PSX's payout shorthand into a human-readable summary.

    Handles three shapes seen in real production data:
      1. '250%(F) (D)'  -- standard: percent(status)(kind), status is F/I
      2. '20%(iii) (D)' -- status can ALSO be a lowercase roman numeral
         (installment number: "iii" = 3rd interim dividend), not just F/I.
         My first pass only handled F/I and silently produced garbled
         output for this case -- real PSX data, not an edge case to ignore.
      3. '23.855376% AT A PREMIUM RS.10/= PER SHARES (R)' -- right issues
         with premium pricing don't fit the percent(status)(kind) pattern
         at all; these get a clean pass-through display rather than a
         failed regex match dumped raw into the UI.

    Always returns a `display` field meant for showing to a person --
    never wrap it in extra parens at the call site, it's already formatted.
    """
    raw = raw.strip()

    m = re.match(r"^([\d.]+)%\s*\(([A-Za-z]+)\)\s*\(([DBR])\)$", raw)
    if m:
        percent, status_raw, kind = m.groups()
        status_key = status_raw.lower()
        kind_label = KIND_LABELS.get(kind.upper(), kind)

        if status_key in ("f", "final"):
            status_label = "final"
        elif status_key in ("i", "interim"):
            status_label = "interim"
        elif status_key in ROMAN_TO_ORDINAL:
            status_label = f"{ROMAN_TO_ORDINAL[status_key]} interim"
        else:
            status_label = status_raw  # unknown code, show as-is rather than guess

        display = f"{percent}% {status_label} {kind_label}"
        return {
            "raw": raw, "percent": float(percent), "status": status_label,
            "kind": kind_label, "display": display, "parse_ok": True,
        }

    # right issue / premium pricing / anything else that doesn't fit the
    # standard shorthand -- pass through cleanly rather than fail silently
    return {
        "raw": raw, "percent": None, "status": None, "kind": None,
        "display": raw, "parse_ok": False,
    }


def parse_date_slash(raw: str) -> str:
    """'18/08/2026' -> '2026-08-18'"""
    return datetime.strptime(raw.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")


DATE_TOKEN_PATTERN = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


def parse_book_closure_range(raw: str) -> tuple[str | None, str | None, str]:
    """Extracts date tokens from the book closure cell. Returns
    (start, end, note) where start/end may be None.

    Three real shapes seen in production data:
      - 2 tokens: the normal case, a single closure window.
      - 0 tokens (just ' - ' or blank): dividend/bonus announced but book
        closure not yet scheduled -- legitimate missing data, NOT an error.
        Still worth keeping the row (the announcement itself is useful),
        just with book closure fields left null.
      - 4 tokens: two closure windows in one cell, seen when a company
        announces two corporate actions together (e.g. cash dividend +
        bonus) with different record dates. Takes the min/max as an outer
        bound rather than guessing which pair belongs to which action --
        safe default, flagged in the note for anyone who wants to split
        them properly later.
    """
    tokens = DATE_TOKEN_PATTERN.findall(raw)

    if len(tokens) == 0:
        return None, None, "book_closure_not_yet_scheduled"

    if len(tokens) == 2:
        return parse_date_slash(tokens[0]), parse_date_slash(tokens[1]), "ok"

    if len(tokens) == 4:
        parsed = sorted(parse_date_slash(t) for t in tokens)
        return parsed[0], parsed[-1], "multiple_closure_windows_collapsed"

    raise ValueError(f"unexpected token count ({len(tokens)}) in book "
                      f"closure cell: {raw!r}")


def parse_announcement_datetime(raw: str) -> tuple[str, str]:
    """'August 10, 2026 4:15 PM' -> ('2026-08-10', '16:15')"""
    dt = datetime.strptime(raw.strip(), "%B %d, %Y %I:%M %p")
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def subtract_working_days(d: datetime, n: int) -> datetime:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri; PSX-specific holiday calendar not applied here
            n -= 1
    return d


T1_TRANSITION_DATE = datetime(2026, 2, 9)


def infer_settlement_lag_days(reference_date: datetime) -> int:
    """T+1 went live 9-Feb-2026; anything before that settled T+2. Applying
    today's lag to a historical pre-transition book closure would silently
    misdate the computed ex-date/last-buy-date for old events."""
    return 1 if reference_date >= T1_TRANSITION_DATE else 2


def parse_row(tr, settlement_lag_days: int | None = None) -> dict | None:
    cells = tr.find_all("td")
    if len(cells) < 6:
        return None

    symbol_link = cells[0].find("a")
    symbol = symbol_link.get_text(strip=True) if symbol_link else None
    company_name = cells[1].get_text(strip=True)
    sector = cells[2].get_text(strip=True)
    dividend = parse_dividend_shorthand(cells[3].get_text())
    annc_date, annc_time = parse_announcement_datetime(cells[4].get_text())
    bc_start, bc_end, bc_note = parse_book_closure_range(cells[5].get_text())

    if bc_start is None:
        # book closure not yet scheduled -- still a real, useful event
        # (a dividend WAS declared), just without derived ex-date/last-buy
        # fields since there's nothing to compute them from yet
        return {
            "id": f"psx-payout-{symbol}-{annc_date}",
            "type": "dividend_announced",
            "status": "pending_book_closure",
            "date": annc_date,
            "time": annc_time,
            "scope": "company",
            "symbol": symbol,
            "sector": sector,
            "company_name": company_name,
            "title": f"{company_name} \u2014 dividend announced: {dividend['display']} (book closure TBD)",
            "source_url": f"https://dps.psx.com.pk/company/{symbol}" if symbol else None,
            "payload": {
                "dividend": dividend,
                "announcement_date": annc_date,
                "announcement_time": annc_time,
                "book_closure_note": bc_note,
            },
            "revision_of": None,
        }

    bc_start_dt = datetime.fromisoformat(bc_start)
    # date-aware: use each event's own regime, not today's -- see
    # infer_settlement_lag_days docstring
    lag = settlement_lag_days if settlement_lag_days is not None \
        else infer_settlement_lag_days(bc_start_dt)
    # ASSUMPTION, not confirmed -- see module docstring
    ex_date_dt = subtract_working_days(bc_start_dt, lag)
    last_buy_dt = subtract_working_days(ex_date_dt, 1)

    return {
        "id": f"psx-payout-{symbol}-{bc_start}",
        "type": "book_closure",
        "status": "confirmed",
        "date": bc_start,
        "time": None,
        "scope": "company",
        "symbol": symbol,
        "sector": sector,
        "company_name": company_name,
        "title": f"{company_name} \u2014 {dividend['display']}",
        # the payouts table has no attachment column of its own (unlike
        # /announcements) -- link to the company's PSX page as a fallback.
        # Getting the real PDF/notice link means cross-referencing this
        # row against parse_announcements.py output by symbol+date, which
        # is a real v2 feature, not built here.
        "source_url": f"https://dps.psx.com.pk/company/{symbol}" if symbol else None,
        "payload": {
            "dividend": dividend,
            "announcement_date": annc_date,
            "announcement_time": annc_time,
            "book_closure_start": bc_start,
            "book_closure_end": bc_end,
            "book_closure_note": bc_note,
            "estimated_ex_date": ex_date_dt.strftime("%Y-%m-%d"),
            "estimated_last_buy_date": last_buy_dt.strftime("%Y-%m-%d"),
            "estimate_assumption": (
                f"settlement_lag_days={lag} "
                f"({'T+1 regime, live since 9-Feb-2026' if lag == 1 else 'pre-T+1, legacy T+2 regime'}) "
                f"-- UNVERIFIED against a live NCCPL/PSX restatement of the "
                f"book-closure offset rule, confirm before relying on this "
                f"for trading decisions"
            ),
        },
        "revision_of": None,
    }


def parse_payouts_html(html: str, settlement_lag_days: int | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("tbody.tbl__body tr")

    events = []
    skipped = 0
    for tr in rows:
        try:
            e = parse_row(tr, settlement_lag_days)
            if e is not None:
                events.append(e)
        except (ValueError, AttributeError, IndexError) as err:
            skipped += 1
            print(f"WARNING: skipped malformed payout row: {err}", file=sys.stderr)

    if skipped:
        print(f"INFO: {skipped}/{len(rows)} rows skipped as malformed", file=sys.stderr)

    return events


if __name__ == "__main__":
    with open("fixtures/payouts_sample.html") as f:
        html = f.read()
    events = parse_payouts_html(html)
    json.dump(events, sys.stdout, indent=2)
