# PSX Calendar

A calendar tracker for Pakistan Stock Exchange events — monetary policy, inflation data, auto sales, corporate results, board meetings, dividends, IPOs, and market holidays — merged into one timeline.

## Sources

| Source | Script | What it covers |
|---|---|---|
| State Bank of Pakistan | `ingest_sbp_mpc.py` | MPC monetary policy calendar (transcribed from SBP's published advance calendar) |
| Pakistan Bureau of Statistics | `ingest_pbs_calendar.py` | Weekly SPI, monthly CPI/WPI release dates (recurrence-generated, no advance calendar exists) |
| PAMA | `ingest_pama.py` | Monthly automobile sales data (recurrence-generated: Monday between the 10th–14th of the following month) |
| PSX holidays | `ingest_psx_holidays.py` | Official market holiday calendar (transcribed from PSX's published notice) |
| PSX company announcements | `fetch_psx_announcements.py` + `parse_announcements.py` | Board meetings, results, AGMs, disclosures — classified from free-text titles |
| PSX payouts | `fetch_psx_payouts.py` + `parse_payouts.py` | Dividends, book closures, computed ex-dates and last-buy-dates |
| PSX notices | `fetch_psx_announcements.py --type E` + `parse_psx_notices.py` | IPOs (subscription, book building, prospectus, listing), delistings, trading halts |

All sources normalize into one shared event schema (see below) and merge via `merge_events.py`.

## Setup

```
pip install requests beautifulsoup4 lxml
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
  --payouts events_psx_payouts.jsonl \
  --out events_merged.json
```

Then open `ui/timeline.html` in a browser and load `events_merged.json`.

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
  "title": "human-readable title",
  "source_url": "link to the source document or null",
  "payload": { "...type-specific fields..." },
  "revision_of": "id of a prior event this supersedes, or null"
}
```

## Known limitations

- **PSX data licensing**: PSX's own terms restrict commercial redistribution of market data without a license (contact `marketdatarequest@psx.com.pk`). This project is built for personal/development use; revisit before any public launch.
- **PAMA and PBS release dates are estimated**, not confirmed by an advance calendar — both publishers only confirm the exact date when they actually release, so `status` reflects that.
- **2027 PSX holidays not yet published** — `ingest_psx_holidays.py` only covers 2026; PSX typically announces the next year's calendar in mid-January.
- **Book closure settlement-lag assumption** (`parse_payouts.py`) is based on PSX's T+1 transition (9-Feb-2026) and validated against one real example (LUCK, Aug 2026) — not exhaustively confirmed across all instrument types.
- **Agenda/PDF text extraction** is not implemented — announcements link to their source PDF/image, but the content isn't parsed or OCR'd.
- **IPO classification** (`parse_psx_notices.py`) is validated against real PSX Notices data but the underlying titles are free text PSX could rephrase at any time.

## Tools

- `inspect_events.py <file>` — type distribution, cancelled count, unclassified sample, sanity checks
- `show_by_type.py <file> <type>` — print all events of one type for spot-checking a classifier
