# NYC BOE election-night scraper

Pulls ED-level results from the NYC Board of Elections live results site
(**https://enr.boenyc.gov**) and emits a tidy CSV:

```
ADED,candidate,votes      # one row per election district per candidate
66002,Layla Law-Gisiko,20
66002,Carl Wilson,59
...
```

`ADED = AD*1000 + ED` (e.g. ED 2 in Assembly District 66 -> `66002`). That key is the
seam everything downstream joins on, so the scraper's only job is to produce it cleanly.

## Files
- `boe_scrape.py` — the scraper (single file, stdlib + `pandas`, `requests`, `bs4`).
- `slate_rollup.py` — rolls the tidy results up into a per-candidate breakdown for our slate
  (see "Slate rollup" below); emits `slate_results.json` for a results site.
- `slate.example.json` — config template for the rollup.
- `samples/` — archived real ENR pages from the **April 28 2026 special election**
  (NYC City Council District 3), used to reverse-engineer the HTML format. Three pages,
  one per drill-down level:
  - `sample-early-results-april.html` — contest summary (borough rows)
  - `…-ed.html` — per-AD breakdown (rows "AD 66", "AD 67", …)
  - `…-ed-details.html` — **ED-level results for a single AD** (rows "ED 2", "ED 3", … with candidate votes) ← the page we actually parse

## Quick start (offline, no network)
```bash
python3 boe_scrape.py \
  --html samples/sample-early-results-april_files/sample-early-results-april-ed-details.html \
  --out live_results.csv
# -> wrote live_results.csv: 125 rows, 25 EDs, 5 candidates
```

## Live usage (election night)
```bash
# point at a contest's results URL; re-poll every 180s
python3 boe_scrape.py --url 'https://enr.boenyc.gov/...' --poll 180 --out live_results.csv
```

## Slate rollup (for the results site)
`slate_rollup.py` turns the scraper output into a per-candidate breakdown for our slate.
Scrape **one contest per CSV** (the natural unit — a contest's name only appears on its own
district's EDs, so each CSV is self-contained), then point the rollup at a `slate.json`:

```bash
# one scrape per race -> results/<race>.csv
python3 boe_scrape.py --url '<NY-7 contest url>'  --out results/ny7.csv
python3 boe_scrape.py --url '<AD-70 contest url>' --out results/ad70.csv

# roll up the whole slate
python3 slate_rollup.py slate.json --out slate_results.json --csv slate_results.csv
```

`slate.json` (see `slate.example.json`):
```json
{
  "title": "Our Slate — 2026 Primary",
  "slate": [
    {"candidate": "Valdez", "district": "NY-7",  "source": "results/ny7.csv"},
    {"candidate": "Conrad", "district": "AD-70", "source": "results/ad70.csv"}
  ]
}
```
- `candidate` = case-insensitive **substring** of the BOE-rendered name (a last name usually
  suffices; must be unambiguous within that contest).

Output `slate_results.json` — one object per slate candidate, ready to render:
```json
{
  "title": "...", "generated_at": "2026-06-23T20:15:00",
  "slate": [
    {"candidate": "Valdez", "district": "NY-7", "matched_name": "Claire Valdez",
     "votes": 1042, "district_total": 3934, "share": 0.2649,
     "eds_reporting": 23, "status": "ok"}
  ]
}
```
`district_total` = total votes counted in that contest (all candidates). `status` is `ok`,
`no-data` (CSV not there yet), `name-not-found` (race not reporting / name format differs), or
`ambiguous` (substring matched >1 candidate — tighten it). Re-run after each scrape to refresh.

## How the parse works (confirmed from the April sample)
The results table is the one containing a `Reported` marker. Within it:
- **col 0** = geography (borough on the summary page; `ED N` on an ED-details page)
- **col 1** = "% Reported"
- **candidate names live in a header ROW** (not column headers); party labels sit in the row below
- `NaN` "spacer" columns sit between real columns; `read_html` also emits mangled
  duplicate tables — the parser ignores anything without a `Reported` marker.

ED-details pages are **per-AD** and titled `[ AD 66 ]`. The parser auto-reads that AD from
the title (`ad_context`) and combines it with each bare `ED N` number to form `ADED`.

## What to edit on the night
The fragile bits are deliberately isolated in two small functions — expect to tweak them
once we see the real 2026 general-election ENR markup:
- **`parse_tables()` / `_geo_to_aded()`** — if the row/column roles shift, or the ED-id
  format differs from the April sample. Use `--html` on a saved page to iterate fast.
- **`crawl()`** — link-following from a start URL. Not yet finalized against the live site
  (the April sample was captured as static pages). If `crawl()` misses ED pages, the
  fallback is to enumerate per-AD URLs directly (observed pattern: `CD<contest>AD<ad><round>.html`,
  e.g. `CD27431AD661.html`).

`NON_CAND` (top of the file) lists header tokens that are *not* candidates — extend it if
the 2026 tables introduce new non-candidate columns.

## Status
- Parse layer (`parse_tables`) — **validated** on the real April-2026 ED-details page
  (125 rows / 25 EDs / 5 candidates, exact vote counts).
- Live navigation (`crawl`) — **to finalize against the live site** (intentionally not
  over-fit to the April capture).

## Downstream (for context, not in this folder)
The `live_results.csv` this produces is consumed by `live_analysis.py` in the parent
project, which joins it to a per-ED reference table (`ed_master.parquet`: demographics +
2024/2025 baselines) and prints the standing election-night questions. The scraper is fully
decoupled from that — its contract is just the `ADED,candidate,votes` CSV above.
