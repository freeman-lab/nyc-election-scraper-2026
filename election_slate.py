"""
NYC BOE election-night scraper + slate rollup — ONE script.

Two ways to run the SAME code:

  • Windmill (Ben):   call `main(sources=...)` -> returns the slate JSON (Windmill serializes it).
  • Local (testing / analysis):
        python3 election_slate.py --json                 # print the slate JSON (what Windmill returns)
        python3 election_slate.py                         # print a human-readable table
        python3 election_slate.py --slate slate.json      # use an external slate config
        python3 election_slate.py --scrape <src> --out results/ny07.csv   # one contest -> tidy CSV (for live_analysis.py etc.)

A "source" is resolved automatically:
    http(s)://…        -> scrape live from the BOE ENR site (crawl a contest's ED pages)
    something.html     -> parse a saved page offline
    something.csv      -> read an already-scraped tidy CSV (ADED,candidate,votes)

Tidy contract (the seam): ADED, candidate, votes   where ADED = AD*1000 + ED.
A candidate only appears on its own district's EDs, so each contest source is self-contained:
    votes          = our candidate's total in that contest
    district_total = total votes counted in that contest (every candidate, every ED)

Windmill deps: pandas, requests, beautifulsoup4, lxml  (lxml is needed by pandas.read_html).
"""
import argparse, io, json, re, sys, time
from datetime import datetime
import pandas as pd

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:                      # offline (.html/.csv) mode still works without these
    requests = None
    BeautifulSoup = None

try:
    import lxml  # noqa: F401  -- pandas.read_html backend; this import makes Windmill install it
except ImportError:
    pass

HEADERS = {'User-Agent': 'Mozilla/5.0 (election-night analysis)'}

# columns in the BOE HTML tables that are NOT candidates (edit if names differ live)
NON_CAND = {'electdist', 'ad', 'ed', 'ad/ed', 'reporting', '% reporting', 'total',
            'public counter', 'votes', 'precinct', 'eds', 'scanner', ''}

# ----------------------------------------------------------------------------------
# OUR SLATE — candidate (substring of the BOE-rendered name) + display label + source.
# `source` is a per-contest results CSV by default; override with live URLs at run time
# (via the `sources` arg in Windmill, or by editing these, or with --slate slate.json).
# ----------------------------------------------------------------------------------
TITLE = "Our Slate — 2026 Primary"
ENR_BASE = "https://enr.boenyc.gov/"     # live NYC BOE results host

# `source` = each contest's SUMMARY page on the live ENR site (Name|Party|Votes|Percentage +
# "Percentage of Scanners Reported"). Confirmed live 2026-06-23. Bare "CDxxxxx0.html" names
# are resolved against ENR_BASE; override with the `sources` arg or --slate for offline tests.
DEFAULT_SLATE = [
    {"candidate": "Valdez",     "district": "NY-07", "source": "CD274520.html"},
    {"candidate": "Avila Chevalier", "district": "NY-13", "source": "CD274790.html"},
    {"candidate": "Kawas",      "district": "SD-12", "source": "CD274720.html"},
    {"candidate": "Brisport",   "district": "SD-25", "source": "CD274860.html"},
    {"candidate": "Moreno",     "district": "AD-36", "source": "CD276150.html"},
    {"candidate": "Kattan",     "district": "AD-37", "source": "CD275830.html"},
    {"candidate": "Orkin",      "district": "AD-38", "source": "CD274680.html"},
    {"candidate": "Celeste Tate", "district": "AD-54", "source": "CD274850.html"},
    {"candidate": "Huntley",    "district": "AD-56", "source": "CD276160.html"},
    {"candidate": "Souffrant",  "district": "AD-57", "source": "CD276820.html"},
    {"candidate": "Sairitupac", "district": "AD-65", "source": "CD275420.html"},
    {"candidate": "Blackburn",  "district": "AD-70", "source": "CD276280.html"},
    {"candidate": "Ocasio-Cortez", "district": "NY-14", "source": "CD276530.html"},
    {"candidate": "Gallagher",  "district": "AD-50", "source": "CD277490.html"},
]


# ============================ SCRAPER (edit live as needed) ============================
def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    return r.text


def _to_aded(electdist):
    """'23053' -> 23053 ; AD = first 2 chars, ED = remainder."""
    s = ''.join(ch for ch in str(electdist) if ch.isdigit())
    return int(s[:2]) * 1000 + int(s[2:]) if len(s) >= 3 else None


def _geo_to_aded(geo, ad_context=None):
    """Map a geography label to ADED. Bare 'ED N' on a per-AD page -> ad_context*1000+N."""
    digits = ''.join(ch for ch in geo if ch.isdigit())
    if not digits:
        return None
    if ad_context is not None and len(digits) <= 3:
        return int(ad_context) * 1000 + int(digits)
    return _to_aded(geo)


def parse_tables(html, ad_context=None):
    """Parse a NYC ENR page -> tidy (ADED, candidate, votes).
    Confirmed April-2026 layout: results table has a 'Reported' marker; col0 = geography,
    col1 = % reported, candidate names in a HEADER ROW, NaN spacer columns. EDIT HERE if
    the live 2026 markup differs."""
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
        running_ad = ad_context              # track AD from 'AD NN' rows as we scan
        for r in range(1, a.shape[0]):
            geo = a[r, 0]
            if not (isinstance(geo, str) and geo.strip()):
                continue
            g = geo.strip()
            m_ad = re.match(r'AD\s*0*(\d+)\s*$', g, re.I)
            if m_ad:                         # 'AD 54' aggregate row -> set context, don't emit
                running_ad = int(m_ad.group(1))
                continue
            m_ed = re.match(r'ED\s*0*(\d+)\s*$', g, re.I)
            if m_ed and running_ad is not None:
                aded = running_ad * 1000 + int(m_ed.group(1))
            else:
                aded = _geo_to_aded(g, running_ad)      # full-id / April-style fallback
            if aded is None:                 # 'Total' / summary rows
                continue
            for j in cand_cols:
                v = pd.to_numeric(str(a[r, j]).replace(',', ''), errors='coerce')
                if pd.notna(v):
                    out.append((aded, names[j].strip(), int(v)))
    return pd.DataFrame(out, columns=['ADED', 'candidate', 'votes'])


def crawl(start_url, max_pages=500):
    """Follow links from a contest start page, collecting ED tables from every page reached.
    EDIT HERE if 2026 navigation differs (which links lead to ED-level tables)."""
    from urllib.parse import urljoin
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
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.find_all('a', href=True):
            nxt = urljoin(url, a['href'])
            if nxt.startswith('http') and 'boenyc' in nxt and nxt not in seen:
                queue.append(nxt)
    if not frames:
        return pd.DataFrame(columns=['ADED', 'candidate', 'votes'])
    return (pd.concat(frames, ignore_index=True)
              .groupby(['ADED', 'candidate'], as_index=False).votes.sum())


# ============================ RESULTS ACQUISITION ============================
def get_results(source):
    """Resolve a source (URL | .html | .csv) -> tidy DataFrame[ADED, candidate, votes]."""
    if source is None:
        return None
    s = str(source)
    if s.startswith('http://') or s.startswith('https://'):
        return crawl(s)
    if s.lower().endswith(('.html', '.htm')):
        with open(s, encoding='utf-8', errors='ignore') as f:
            return parse_tables(f.read())
    if s.lower().endswith('.csv'):
        df = pd.read_csv(s)
        df['votes'] = pd.to_numeric(df['votes'], errors='coerce').fillna(0).astype(int)
        df['candidate'] = df['candidate'].astype(str)
        return df
    raise ValueError(f"unrecognized source (need http(s)/.html/.csv): {source}")


# ============================ CONTEST SUMMARY (slate path) ============================
def parse_contest_summary(html):
    """Parse a contest SUMMARY page -> (DataFrame[candidate, party, votes], reported_pct).
    Live 2026 layout (e.g. CD274520.html): a 'Name | Party | Votes | Percentage' table plus a
    'Percentage of Scanners Reported: N %' line. EDIT HERE if the live columns shift."""
    rep = None
    m = re.search(r'Scanners Reported:\s*([\d.]+)\s*%', html)
    if m:
        rep = float(m.group(1))
    rows = []
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.DataFrame(columns=['candidate', 'party', 'votes']), rep
    for t in tables:
        cols = {str(c).strip(): c for c in t.columns}
        if 'Name' not in cols or 'Votes' not in cols:
            continue
        for _, r in t.iterrows():
            nm = r[cols['Name']]
            if not isinstance(nm, str) or not nm.strip() or nm.strip().lower() == 'nan':
                continue
            low = nm.lower()
            if 'scanners reported' in low:               # status line, not a candidate
                m2 = re.search(r'([\d.]+)\s*%', nm)
                if m2:
                    rep = float(m2.group(1))
                continue
            if 'information as of' in low:
                continue
            v = pd.to_numeric(str(r[cols['Votes']]).replace(',', ''), errors='coerce')
            party = str(r[cols['Party']]).strip() if 'Party' in cols else ''
            rows.append({'candidate': nm.strip(),
                         'party': '' if party.lower() == 'nan' else party,
                         'votes': int(v) if pd.notna(v) else 0})
        if rows:
            break
    return pd.DataFrame(rows, columns=['candidate', 'party', 'votes']), rep


def load_contest(source):
    """Resolve a slate source -> (DataFrame[candidate, party, votes], reported_pct).
    bare 'CDxxxxx0.html' or http(s) -> live ENR summary page; local .html -> offline;
    .csv -> a saved tidy candidate,votes table (for testing)."""
    import os
    s = str(source)
    if s.lower().endswith('.csv'):
        df = pd.read_csv(s)
        df['votes'] = pd.to_numeric(df['votes'], errors='coerce').fillna(0).astype(int)
        df['candidate'] = df['candidate'].astype(str)
        if 'party' not in df:
            df['party'] = ''
        return df[['candidate', 'party', 'votes']], None
    if s.startswith('http://') or s.startswith('https://'):
        return parse_contest_summary(fetch(s))
    if s.lower().endswith(('.html', '.htm')):
        if os.path.exists(s):
            with open(s, encoding='utf-8', errors='ignore') as f:
                return parse_contest_summary(f.read())
        return parse_contest_summary(fetch(ENR_BASE + s))     # bare page name -> live
    raise ValueError(f"unrecognized source: {source}")


# ============================ SLATE ROLLUP ============================
def _rollup_entry(entry, cache):
    label = {'candidate': entry['candidate'], 'district': entry.get('district', '')}
    src = entry['source']
    if src not in cache:
        try:
            cache[src] = load_contest(src)
        except Exception:
            cache[src] = (None, None)
    df, rep = cache[src]
    if df is None or len(df) == 0:
        return {**label, 'matched_name': None, 'votes': 0, 'district_total': 0,
                'share': None, 'reported_pct': rep, 'status': 'no-data'}

    district_total = int(df['votes'].sum())
    needle = entry['candidate'].strip().lower()
    matched = df[df['candidate'].str.lower().str.contains(needle, regex=False)]
    names = sorted(matched['candidate'].unique())
    if not names:
        status, votes, matched_name = 'name-not-found', 0, None
    elif len(names) > 1:
        status, votes, matched_name = 'ambiguous', int(matched['votes'].sum()), '; '.join(names)
    else:
        status, votes, matched_name = 'ok', int(matched['votes'].sum()), names[0]
    if status == 'ok' and district_total == 0:        # page is live but no votes counted yet
        status = 'waiting'
    share = round(votes / district_total, 4) if district_total else None
    # full contest roster (for margins / leader in close races); our candidate flagged `ours`
    all_candidates = [
        {'name': r2['candidate'], 'party': r2['party'], 'votes': int(r2['votes']),
         'share': round(r2['votes'] / district_total, 4) if district_total else None,
         'ours': r2['candidate'] == matched_name}
        for _, r2 in df.sort_values('votes', ascending=False).iterrows()
    ]
    return {**label, 'matched_name': matched_name, 'votes': votes,
            'district_total': district_total, 'share': share,
            'reported_pct': rep, 'status': status, 'all_candidates': all_candidates}


def build_payload(slate, title=TITLE):
    cache = {}
    rows = [_rollup_entry(e, cache) for e in slate]
    return {'title': title,
            'generated_at': datetime.now().isoformat(timespec='seconds'),
            'slate': rows}


# ============================ WINDMILL ENTRYPOINT ============================
def main(sources: dict = None, slate: list = None, title: str = TITLE) -> dict:
    """Returns the slate results JSON.
    sources: optional {district_label: url_or_path} overriding each contest's source
             (e.g. the live BOE URLs on the night). slate: optional full override of the
             candidate list (else the baked-in DEFAULT_SLATE is used)."""
    s = [dict(e) for e in (slate or DEFAULT_SLATE)]
    if sources:
        for e in s:
            if e['district'] in sources:
                e['source'] = sources[e['district']]
    return build_payload(s, title=title)


# ============================ LOCAL CLI ============================
def _print_table(payload):
    print(f"{payload['title']}  ({payload['generated_at']})")
    print(f"{'candidate':24} {'district':8} {'votes':>8} {'district':>10} {'share':>7} {'rptd':>6}  status")
    for r in payload['slate']:
        sh = f"{100*r['share']:.1f}%" if r['share'] is not None else "   -"
        rp = f"{r['reported_pct']:.0f}%" if r.get('reported_pct') is not None else "   -"
        flag = '' if r['status'] == 'ok' else f"  <-- {r['status']}"
        name = r['matched_name'] or r['candidate']
        print(f"{name[:24]:24} {r['district'][:8]:8} {r['votes']:>8,} "
              f"{r['district_total']:>10,} {sh:>7} {rp:>6}{flag}")


def _cli():
    ap = argparse.ArgumentParser(description="BOE scraper + slate rollup (one script).")
    ap.add_argument('--slate', help='external slate.json config (else uses baked-in DEFAULT_SLATE)')
    ap.add_argument('--json', action='store_true', help='print slate results as JSON (what Windmill returns)')
    ap.add_argument('--csv', help='also write the slate breakdown to a flat CSV')
    ap.add_argument('--scrape', help='scrape ONE source (url/.html) and write a tidy ADED,candidate,votes CSV')
    ap.add_argument('--out', help='output path for --scrape')
    a = ap.parse_args()

    if a.scrape:                                  # one-contest tidy CSV (for live_analysis.py / analysis)
        df = get_results(a.scrape)
        out = a.out or 'live_results.csv'
        df.to_csv(out, index=False)
        print(f"wrote {out}: {len(df)} rows, "
              f"{df.ADED.nunique() if len(df) else 0} EDs, "
              f"{df.candidate.nunique() if len(df) else 0} candidates")
        return

    if a.slate:
        cfg = json.load(open(a.slate))
        payload = build_payload(cfg['slate'], title=cfg.get('title', TITLE))
    else:
        payload = main()

    if a.csv:
        pd.DataFrame(payload['slate']).to_csv(a.csv, index=False)
    if a.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_table(payload)
        if a.csv:
            print(f"\nwrote {a.csv}")


if __name__ == '__main__':
    _cli()
