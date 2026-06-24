"""
Slate rollup -> a per-candidate results breakdown for OUR slate, ready to render
on a simple results site.

Pipeline:
    boe_scrape.py  --(one contest per scrape)-->  results/<race>.csv   (ADED,candidate,votes)
    slate_rollup.py  --(slate.json)-->            slate_results.json

Key idea: a candidate only appears on the ballot in their own district's EDs, so each
per-contest results CSV is self-contained. For each slate candidate we report:
    votes            total for our candidate across that contest's EDs
    district_total   total votes counted in that contest (every candidate, every ED)
    share            votes / district_total

slate.json
----------
    {
      "title": "Our Slate — 2026 Primary",
      "slate": [
        {"candidate": "Valdez",  "district": "NY-7",  "source": "results/ny7.csv"},
        {"candidate": "Conrad",  "district": "AD-70", "source": "results/ad70.csv"}
      ]
    }
  - "candidate": a case-insensitive SUBSTRING of the name as BOE renders it (a last name
    is usually enough; must be unambiguous within that contest).
  - "district": free-text display label for the site.
  - "source":   path to that contest's tidy results CSV (from boe_scrape.py).

Usage
-----
    python3 slate_rollup.py slate.json --out slate_results.json
    python3 slate_rollup.py slate.json --csv slate_results.csv      # optional flat CSV too
"""
import argparse, json, sys
from datetime import datetime
import pandas as pd


def load_results(path):
    df = pd.read_csv(path)
    df['votes'] = pd.to_numeric(df['votes'], errors='coerce').fillna(0).astype(int)
    df['candidate'] = df['candidate'].astype(str)
    return df


def rollup_entry(entry, cache):
    label = {'candidate': entry['candidate'], 'district': entry.get('district', '')}
    src = entry['source']
    if src not in cache:
        try:
            cache[src] = load_results(src)
        except FileNotFoundError:
            cache[src] = None
    df = cache[src]
    if df is None or df.empty:
        return {**label, 'matched_name': None, 'votes': 0,
                'district_total': 0, 'share': None, 'eds_reporting': 0, 'status': 'no-data'}

    district_total = int(df['votes'].sum())
    eds_reporting = int(df.loc[df['votes'] > 0, 'ADED'].nunique())

    needle = entry['candidate'].strip().lower()
    matched = df[df['candidate'].str.lower().str.contains(needle, regex=False)]
    names = sorted(matched['candidate'].unique())
    if not names:
        status, votes, matched_name = 'name-not-found', 0, None
    elif len(names) > 1:                       # substring hit >1 candidate -> tighten the config
        status, votes, matched_name = 'ambiguous', int(matched['votes'].sum()), '; '.join(names)
    else:
        status, votes, matched_name = 'ok', int(matched['votes'].sum()), names[0]

    share = round(votes / district_total, 4) if district_total else None
    return {**label, 'matched_name': matched_name, 'votes': votes,
            'district_total': district_total, 'share': share,
            'eds_reporting': eds_reporting, 'status': status}


def run(slate_path, out_json, out_csv=None):
    cfg = json.load(open(slate_path))
    cache = {}
    rows = [rollup_entry(e, cache) for e in cfg['slate']]
    payload = {
        'title': cfg.get('title', 'Slate results'),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'slate': rows,
    }
    with open(out_json, 'w') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    if out_csv:
        pd.DataFrame(rows).to_csv(out_csv, index=False)

    # console summary
    print(f"{payload['title']}  ({payload['generated_at']})")
    print(f"{'candidate':22} {'district':10} {'votes':>8} {'district':>10} {'share':>7}  status")
    for r in rows:
        sh = f"{100*r['share']:.1f}%" if r['share'] is not None else "   -"
        flag = '' if r['status'] == 'ok' else f"  <-- {r['status']}"
        name = r['matched_name'] or r['candidate']
        print(f"{name[:22]:22} {r['district'][:10]:10} {r['votes']:>8,} "
              f"{r['district_total']:>10,} {sh:>7}{flag}")
    print(f"\nwrote {out_json}" + (f" and {out_csv}" if out_csv else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slate', help='slate.json config')
    ap.add_argument('--out', default='slate_results.json')
    ap.add_argument('--csv', default=None)
    a = ap.parse_args()
    run(a.slate, a.out, a.csv)


if __name__ == '__main__':
    main()
