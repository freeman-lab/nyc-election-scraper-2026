# NYC BOE election-night scraper + slate rollup

One script — **`election_slate.py`** — that reads live results from the NYC Board of Elections
ENR site (**https://enr.boenyc.gov**) and produces a per-candidate breakdown for our slate,
ready to render on a simple results site. It also doubles as an ED-level scraper for separate
demographic analysis.

## Two paths, one script
| | **Slate** (the site / Windmill) | **Analysis** (`live_analysis.py`, separate project) |
|---|---|---|
| BOE page | contest **summary** (`CDxxxxx0.html`) | **ED-level** (`CDxxxxxAD<county>.html`) |
| parser | `parse_contest_summary` | `parse_tables` |
| produces | slate JSON (`main()` / `--json`) | tidy `ADED,candidate,votes` CSV (`--scrape`) |

The slate path needs no ED data — BOE's summary page gives each contest's candidate totals and
district total directly. The ED path is only for demographic reads.

## Windmill (the slate JSON)
Paste the whole `election_slate.py` into a Windmill **Python** script. Windmill auto-detects
`main()` and builds the args form. **Run with no args** — it returns the live slate JSON for the
12 baked-in contests.

```
main(sources: dict = None, slate: list = None, title: str = "Our Slate — 2026 Primary") -> dict
```
- `sources` (optional) — override a contest's page per district, e.g.
  `{"NY-07": "https://enr.boenyc.gov/CD274520.html"}` (only if BOE changes a page id).
- `slate` (optional) — replace the whole candidate list.
- `title` (optional) — dashboard title.

Deps auto-install from imports: `pandas`, `requests`, `beautifulsoup4`, `lxml`
(the guarded `import lxml` ensures Windmill provisions `read_html`'s backend).
Schedule it every 1–2 min to refresh as results come in.

### Output shape
```json
{
  "title": "...", "generated_at": "2026-06-23T21:30:00",
  "slate": [
    {"candidate": "Valdez", "district": "NY-07", "matched_name": "Claire Valdez",
     "votes": 14820, "district_total": 31240, "share": 0.4744,
     "reported_pct": 61.0, "status": "ok",
     "all_candidates": [
       {"name": "Claire Valdez", "party": "Democratic", "votes": 14820, "share": 0.4744, "ours": true},
       {"name": "Vichal Kumar",  "party": "Democratic", "votes": 9434,  "share": 0.302,  "ours": false}
     ]}
  ]
}
```
- `votes` our candidate · `district_total` all votes counted in the contest · `share` = ratio (0–1)
- `reported_pct` BOE "% of scanners reported" · `matched_name` full BOE-rendered name
- `all_candidates` full contest field, sorted by votes, `ours:true` on our candidate (for margins / leader)
- `status`: `ok` | `waiting` (live but 0 counted) | `name-not-found` | `ambiguous` | `no-data`

`sample_slate_results.json` is an illustrative sample (real names/structure, fake numbers).

## Local usage
```bash
python3 election_slate.py                 # human-readable slate table (hits the live site)
python3 election_slate.py --json          # the JSON Windmill returns
python3 election_slate.py --slate slate.json   # external config (sources can be local .html/.csv)

# ED-level scrape for analysis -> tidy ADED,candidate,votes CSV
python3 election_slate.py --scrape <url|saved.html> --out results/ny07.csv
```

## slate.json
Our 12 candidates (`slate.json`; template in `slate.example.json`). Each entry:
`{"candidate": "<substring of BOE name>", "district": "<label>", "source": "<page or csv>"}`.
`candidate` is a case-insensitive substring (a last name usually; must be unambiguous in that
contest). The baked-in `DEFAULT_SLATE` inside `election_slate.py` uses the live contest pages
directly; `slate.json` is the editable external copy.

## Live ENR structure (confirmed 2026-06-23)
- Borough index: `index.html` → `C1`=Manhattan, `C2`=Bronx, `C3`=Kings/Brooklyn, `C4`=Queens,
  `C5`=Richmond/SI. Each lists its contests by name → a contest page `CDxxxxx0.html`.
- **Contest summary** `CDxxxxx0.html` — `Name | Party | Votes | Percentage` table + a
  "Percentage of Scanners Reported: N %" line. Multi-borough contests resolve to one full-district
  page (NY-07 = `CD274520.html` from both Brooklyn and Queens). ← slate path.
- **ED level**: contest → AD-details index `CDxxxxxADI0.html` → per-county pages
  `CDxxxxxAD<county>.html` with rows `AD NN` then `ED N`. Same header-row layout as the archived
  April sample, but **no `[ AD NN ]` bracket** — the ED parser tracks the AD from `AD NN` rows.
  These pages are stubs (votes shown as `-`) until results post. ← analysis path, finalize live.

## samples/
Archived real ENR pages from the **April 28 2026** City Council District 3 special election,
used to reverse-engineer the ED-level format. Offline test:
```bash
python3 election_slate.py --scrape \
  samples/sample-early-results-april_files/sample-early-results-april-ed-details.html \
  --out /tmp/ccd3.csv      # -> 125 rows, 25 EDs, 5 candidates
```

## What to edit on the night
The fragile parsing is isolated in `parse_contest_summary` (slate) and `parse_tables` /
`_geo_to_aded` (ED level). If the live markup shifts, save a page and iterate with `--scrape`
(or call the parser directly) until the output looks right — everything downstream is decoupled
by the JSON / `ADED,candidate,votes` contracts.
