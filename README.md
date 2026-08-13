# PSX Calendar

A calendar tracker for Pakistan Stock Exchange events — monetary policy, inflation data, auto sales, corporate results, board meetings, dividends, IPOs, and market holidays — merged into one timeline, with company logos, real scheduled meeting dates, and sentiment classification layered on top.

## Sources

| Source | Script | What it covers |
|---|---|---|
| State Bank of Pakistan | `ingest_sbp_mpc.py` | MPC monetary policy calendar (transcribed from SBP's published advance calendar) |
| Pakistan Bureau of Statistics | `ingest_pbs_calendar.py` | Weekly SPI, monthly CPI/WPI release dates (recurrence-generated; date-aware confirmed/estimated status) |
| PAMA | `ingest_pama.py` | Monthly automobile sales data (Monday between the 10th–14th of the following month, confirmed against real observation) |
| PSX holidays | `ingest_psx_holidays.py` | Official market holiday calendar (transcribed from PSX's published notice; 2026 only, PSX hasn't published 2027 yet) |
| PSX company announcements | `fetch_psx_announcements.py` + `parse_announcements.py` | Board meetings, results, AGMs, disclosures — classified from free-text titles |
| PSX payouts | `fetch_psx_payouts.py` + `parse_payouts.py` | Dividends, book closures, computed ex-dates and last-buy-dates |
| PSX notices | `fetch_psx_announcements.py --type E` + `parse_psx_notices.py` | IPOs (subscription, book building, prospectus, listing), delistings, trading halts |

All sources normalize into one shared event schema (see below) and merge via `merge_events.py`.

## Enrichment (optional, after the base merge)

These add real data on top of the merged calendar — none are required to use the timeline, but each closes a real gap:

| Script | What it does | Cost |
|---|---|---|
| `fetch_company_domains.py` + `attach_logos.py` | Scrapes each company's real website off their PSX profile page, attaches a real logo image via logo.dev | Free (logo.dev free tier, needs a publishable key) |
| `extract_meeting_dates.py` | Replaces board meeting/AGM filing dates with the REAL scheduled meeting date/time, extracted from the notice PDF (text extraction first, Gemini vision OCR fallback for scanned PDFs — confirmed ~85% of these notices are scanned images with no text layer) | Free (Gemini free tier) |
| `classify_sentiment.py` | Adds positive/negative/neutral/mixed sentiment to results, disclosures, and material announcements | Free (Gemini free tier) |

Both Gemini-based scripts need a free API key (no credit card) from https://aistudio.google.com, set as `GEMINI_API_KEY`. Same key works for both.

## Setup

```
pip install requests beautifulsoup4 lxml pdfplumber python-dateutil pymupdf
```

## Usage

Generate each source, then merge:

```
python ingest_sbp_mpc.py > events_sbp.json
python ingest_pbs_calendar.py --from 2026-08-01 --to 2027-08-01 > events_pbs.json
python ingest_pama.py --from 2026-08-01 --to 2027-08-01 > events_pama.json
python ingest_psx_holidays.py > events_holidays.json

python fetch_psx_announcements.py --from 2026-06-01 --to 2026-08-11 --out events_psx_announcements.jsonl
python fetch_psx_announcements.py --from 2026-06-01 --to 2026-08-11 --type E --out events_psx_notices.jsonl
python fetch_psx_payouts.py --out events_psx_payouts.jsonl

python merge_events.py \
  --sbp events_sbp.json \
  --pbs events_pbs.json \
  --pama events_pama.json \
  --holidays events_holidays.json \
  --announcements events_psx_announcements.jsonl \
  --notices events_psx_notices.jsonl \
  --payouts events_psx_payouts.jsonl \
  --out events_merged.json
```

Then, optionally, enrich the merged file (each is independent, run whichever you want):

```
set GEMINI_API_KEY=your_free_key_here

python fetch_company_domains.py --from-events events_merged.json
python attach_logos.py events_merged.json --out events_merged.json

python extract_meeting_dates.py events_merged.json --out events_merged.json

python classify_sentiment.py events_merged.json --out events_merged.json
```

Then open `ui/timeline.html` in a browser (a real browser tab, not Claude's in-chat preview — its sandbox blocks external image domains like logo.dev) and load `events_merged.json`.

## Event schema

Every source emits events matching this shape:

```json
{
  "id": "unique-per-event",
  "type": "board_meeting | book_closure | mpc | spi_weekly | market_holiday | ipo_subscription | ...",
  "status": "confirmed | estimated | cancelled | pending_book_closure",
  "date": "YYYY-MM-DD",
  "time": "HH:MM or null",
  "scope": "market | sector | company",
  "symbol": "PSX ticker or null",
  "sector": "sector name or null",
  "company_name": "or null",
  "title": "human-readable title",
  "source_url": "link to the source document or null",
  "logo_url": "real company logo, only present after attach_logos.py runs",
  "sentiment": "positive | negative | neutral | mixed, only present after classify_sentiment.py runs",
  "payload": { "...type-specific fields..." },
  "revision_of": "id of a prior event this supersedes, or null"
}
```

## Known limitations

- **PSX data licensing**: PSX's own terms restrict commercial redistribution of market data without a license (contact `marketdatarequest@psx.com.pk`). This project is built for personal/development use; revisit before any public launch.
- **PAMA and PBS release dates are estimated**, not confirmed by an advance calendar — both publishers only confirm the exact date when they actually release, so `status` reflects that.
- **2027 PSX holidays not yet published** — `ingest_psx_holidays.py` only covers 2026; PSX typically announces the next year's calendar in mid-January.
- **Meeting-date extraction has a real, honest gap**: even with the Gemini vision fallback, some notices resolve to nothing — the notice's `date` field then still reflects the *filing* date, not the real meeting date, and that's flagged in `payload.date_source` (absent = still filing date; `text` or `vision` = a real scheduled date was found).
- **`time` fields are mostly null on purpose**: PSX's own table gives filing time, not scheduled event time, for board meetings/AGMs/briefings — showing that as if it were the event's start time was actively misleading, so it's suppressed unless `extract_meeting_dates.py` has found a real one. The original filing time is preserved in `payload.filed_time`.
- **Company logos aren't 100% coverage**: fund composites, ETFs, and some smaller companies don't have a resolvable public website; those fall back to a colored-initials avatar in the UI, by design.
- **Real IPO terms** (price, shares offered) are not extracted — the IPO tab's dedicated columns exist but stay empty pending that work.
- **Book closure settlement-lag assumption** (`parse_payouts.py`) is based on PSX's T+1 transition (9-Feb-2026) and validated against one real example (LUCK, Aug 2026) — not exhaustively confirmed across all instrument types.

## Tools

- `inspect_events.py <file>` — type distribution, cancelled count, unclassified sample, sanity checks
- `show_by_type.py <file> <type>` — print all events of one type for spot-checking a classifier
- `diagnose_meeting_dates.py <file>` — shows which unresolved meeting-date events are likely retrospective (no future date to find) vs genuinely worth a closer look
- `diagnose_company_page.py <symbol>` — checks whether a PSX company profile page's content is present in a plain HTTP response (diagnostic used to confirm the logo-scraping approach)
