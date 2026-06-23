"""
BOE election-night scraper -> tidy per-ED results (the seam live_analysis.py consumes).

Output CSV columns:  ADED, candidate, votes   (one row per ED per candidate)

The NYC ENR site (https://enr.boenyc.gov) is HTML, and its exact 2026 structure is
unknown until it goes live. So the design keeps the *fragile* parsing in two small,
editable functions (`parse_tables`, `crawl`) and everything else stable.

Usage
-----
  # OFFLINE: save a results page to a file, iterate the parser without hitting the site
  python3 boe_scrape.py --html saved_page.html --out live_results.csv

  # LIVE: crawl from a contest/results URL, poll every N seconds
  python3 boe_scrape.py --url 'https://enr.boenyc.gov/...' --poll 180 --out live_results.csv

On the night: open a contest's ED-level page in a browser, Save-As HTML, run --html on it,
eyeball the output, tweak parse_tables() until the tidy CSV looks right, then point --url at it.
"""
import argparse, sys, time, io, re
import pandas as pd

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None; BeautifulSoup = None

HEADERS = {'User-Agent': 'Mozilla/5.0 (election-night analysis)'}

# --- columns in the BOE HTML tables that are NOT candidates (edit if names differ) ---
NON_CAND = {'electdist', 'ad', 'ed', 'ad/ed', 'reporting', '% reporting', 'total',
            'public counter', 'votes', 'precinct', 'eds', 'scanner', ''}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    return r.text


def _to_aded(electdist):
    """'23053' -> 23053 ; AD = first 2 chars, ED = remainder (matches ed_master ADED)."""
    s = ''.join(ch for ch in str(electdist) if ch.isdigit())
    if len(s) < 3:
        return None
    return int(s[:2]) * 1000 + int(s[2:])


def parse_tables(html, ad_context=None):
    """Parse a NYC ENR page -> tidy (ADED, candidate, votes).

    REAL ENR layout (confirmed from a 2026 sample):
      * results table is the one containing a 'Reported' marker
      * column 0 = geography (borough name on the summary page; ED id on an ED page)
      * column 1 = Reported %
      * candidate names live in a HEADER ROW (row 0), party labels in the row below
      * NaN 'spacer' columns sit between real columns
    Only rows whose geography is an ED id (digits) become ADED rows; borough/Total rows
    are skipped. `ad_context` (AD number from the page/URL) is used if ED pages list bare
    ED numbers instead of full AD+ED ids.
    EDIT HERE if 2026 shifts the row/column roles.
    """
    out = []
    if ad_context is None:                      # ED-detail pages are per-AD, titled "[ AD 66 ]"
        m = re.search(r'\[\s*AD\s*(\d+)\s*\]', html)
        if m:
            ad_context = int(m.group(1))
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.DataFrame(columns=['ADED', 'candidate', 'votes'])
    for t in tables:
        a = t.dropna(axis=1, how='all').values
        if a.shape[0] < 3 or 'Reported' not in ' '.join(str(x) for x in a.flatten()):
            continue
        names = a[0]
        cand_cols = [j for j in range(len(names))
                     if isinstance(names[j], str) and names[j].strip() and j >= 2]
        for r in range(1, a.shape[0]):
            geo = a[r, 0]
            if not (isinstance(geo, str) and geo.strip()):
                continue
            aded = _geo_to_aded(geo.strip(), ad_context)
            if aded is None:               # borough/Total/summary row -> not an ED
                continue
            for j in cand_cols:
                v = pd.to_numeric(str(a[r, j]).replace(',', ''), errors='coerce')
                if pd.notna(v):
                    out.append((aded, names[j].strip(), int(v)))
    return pd.DataFrame(out, columns=['ADED', 'candidate', 'votes'])


def _geo_to_aded(geo, ad_context=None):
    """Map a geography label to ADED. Full AD+ED id ('23053') -> 23053.
    Bare ED number on an AD page -> ad_context*1000 + ED. Borough/Total -> None.
    FINALIZE once we see an actual ED-level page (the exact ED-id format)."""
    digits = ''.join(ch for ch in geo if ch.isdigit())
    if not digits:
        return None
    if ad_context is not None and len(digits) <= 3:
        return int(ad_context) * 1000 + int(digits)
    return _to_aded(geo)


def crawl(start_url, max_pages=500):
    """Follow links from a start page, collecting ED tables from every page reached.
    EDIT HERE if 2026's navigation differs (which links lead to ED-level tables)."""
    seen, queue, frames = set(), [start_url], []
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  skip {url}: {e}", file=sys.stderr); continue
        df = parse_tables(html)
        if len(df):
            frames.append(df)
        # enqueue same-host result links (tune the filter on the night)
        soup = BeautifulSoup(html, 'html.parser')
        from urllib.parse import urljoin
        for a in soup.find_all('a', href=True):
            nxt = urljoin(url, a['href'])
            if nxt.startswith('http') and 'boenyc' in nxt and nxt not in seen:
                queue.append(nxt)
    if not frames:
        return pd.DataFrame(columns=['ADED', 'candidate', 'votes'])
    return pd.concat(frames, ignore_index=True).groupby(['ADED', 'candidate'], as_index=False).votes.sum()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--html'); ap.add_argument('--url'); ap.add_argument('--out', default='live_results.csv')
    ap.add_argument('--poll', type=int, default=0)
    a = ap.parse_args()

    def once():
        if a.html:
            df = parse_tables(open(a.html, encoding='utf-8', errors='ignore').read())
        else:
            df = crawl(a.url)
        df.to_csv(a.out, index=False)
        print(f"wrote {a.out}: {len(df)} rows, {df.ADED.nunique() if len(df) else 0} EDs, "
              f"{df.candidate.nunique() if len(df) else 0} candidates")

    once()
    while a.poll and a.url:
        time.sleep(a.poll); once()


if __name__ == '__main__':
    main()
