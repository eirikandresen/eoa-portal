"""
update_nav.py — EOA Capital AS
Henter daglig NAV for Pareto Aksje Norge A + OSEBX benchmark.

Kilderekkefølge NAV:
  1. paretoam.com (andelsklasse A)
  2. Yahoo Finance (0P00001F9P.IR, 0P0001BNTE.F, POAKTNY.OL)
  3. Morningstar NO timeseries API

Kilde OSEBX:
  1. Yahoo Finance (^OSEBX)

Kjøres automatisk via GitHub Actions hver ukedag kl. 20:00 Oslo-tid.
Kan også kjøres manuelt fra Actions-fanen.
"""

import json
import re
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent
OUT  = ROOT / 'nav_data.json'
XLSX = ROOT / 'PAN_A_-_daglig_nav.xlsx'

MS_ID        = 'F0GBR04OMP'
MS_UNIVERSE  = 'FONOR$$ALL'
MS_TOKENS    = ['dr6pz9spfi', 't92wz0sj7c', 'okhysb8aoh']
YAHOO_TICKERS = ['0P00001F9P.IR', '0P0001BNTE.F', 'POAKTNY.OL']

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ─── helpers ────────────────────────────────────────────────────────────────

def load():
    if OUT.exists():
        with open(OUT) as f:
            return json.load(f)
    return []

def save(data):
    data.sort(key=lambda r: r['d'])
    with open(OUT, 'w') as f:
        json.dump(data, f, separators=(',', ':'))

def last_date(data):
    return data[-1]['d'] if data else '2021-12-19'

def last_osebx(data):
    for r in reversed(data):
        if r.get('o') and r['o'] > 100:
            return r['o']
    return None

def valid_nav(v, last_known=None):
    if v is None or v < 1000:
        return False
    if last_known and (v > last_known * 1.3 or v < last_known * 0.7):
        return False
    return True

def valid_osebx(v, last_known=None):
    if v is None or v < 100:
        return False
    if last_known and (v > last_known * 1.35 or v < last_known * 0.65):
        return False
    return True

def to_iso(ddmmyyyy):
    d, m, y = ddmmyyyy.split('.')
    return f'{y}-{m.zfill(2)}-{d.zfill(2)}'

# ─── NAV sources ─────────────────────────────────────────────────────────────

def fetch_pareto(last_known_nav):
    """Scrape paretoam.com — andelsklasse A."""
    try:
        url = 'https://paretoam.com/funds/pareto-aksje-norge?country=norway'
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            print(f'Pareto HTTP {r.status_code}')
            return None

        html = r.text

        # Try multiple regex patterns for robustness
        patterns = [
            r'A\s*[|│]\s*NOK\s*[|│]\s*(\d{2}\.\d{2}\.\d{4})\s*[|│]\s*([\d\s]+\.[\d]+)',
            r'class="[^"]*andelsklasse[^"]*"[^>]*>\s*A\s*<.*?(\d{2}\.\d{2}\.\d{4}).*?([\d]+\.[\d]+)',
            r'>A<.*?NOK.*?(\d{2}\.\d{2}\.\d{4}).*?([\d\s]{5,10}\.[\d]{2,4})',
        ]

        for pattern in patterns:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                date_str = to_iso(m.group(1).strip())
                nav = float(m.group(2).replace(' ', '').replace('\xa0', ''))
                if valid_nav(nav, last_known_nav):
                    print(f'Pareto: {date_str} NAV={nav}')
                    return [{'d': date_str, 'n': nav}]

        # Fallback: find any number that looks like a NAV near "A"
        # Look for the table row pattern
        rows = re.findall(r'(\d{2}\.\d{2}\.\d{4})[^0-9]+([\d]{4,5}\.[\d]{2,4})', html)
        for date_raw, nav_raw in rows:
            nav = float(nav_raw)
            if valid_nav(nav, last_known_nav):
                date_str = to_iso(date_raw)
                print(f'Pareto (fallback): {date_str} NAV={nav}')
                return [{'d': date_str, 'n': nav}]

        print('Pareto: no NAV found in HTML')
        return None
    except Exception as e:
        print(f'Pareto failed: {e}')
        return None


def fetch_yahoo_nav(from_date, last_known_nav):
    """Yahoo Finance — try multiple tickers."""
    start = int(datetime.strptime(from_date, '%Y-%m-%d').timestamp())
    end   = int((datetime.now() + timedelta(days=2)).timestamp())

    for ticker in YAHOO_TICKERS:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&period1={start}&period2={end}'
            r = requests.get(url, headers=HEADERS, timeout=12)
            if not r.ok:
                continue
            d = r.json()
            result = d.get('chart', {}).get('result', [])
            if not result:
                continue
            timestamps = result[0].get('timestamp', [])
            closes = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])
            rows = []
            for t, c in zip(timestamps, closes):
                if c is None:
                    continue
                nav = round(float(c), 4)
                if valid_nav(nav, last_known_nav):
                    d_str = (datetime.utcfromtimestamp(t) + timedelta(hours=1)).strftime('%Y-%m-%d')
                    rows.append({'d': d_str, 'n': nav})
            if rows:
                print(f'Yahoo NAV ({ticker}): {len(rows)} rows, last={rows[-1]}')
                return rows
        except Exception as e:
            print(f'Yahoo NAV {ticker} failed: {e}')

    return None


def fetch_morningstar_nav(from_date):
    """Morningstar timeseries API — try multiple tokens."""
    for token in MS_TOKENS:
        try:
            url = (
                f'https://tools.morningstar.no/api/rest.svc/timeseries_price/{token}'
                f'?currencyId=NOK&idtype=Morningstar&frequency=daily&outputType=JSON'
                f'&startDate={from_date}'
                f'&id={MS_ID}]2]1]{MS_UNIVERSE}'
            )
            r = requests.get(url, headers=HEADERS, timeout=15)
            if not r.ok:
                continue
            d = r.json()
            series = d.get('TimeSeries', {}).get('Security', [{}])[0].get('HistoryDetail', [])
            if not series:
                continue
            rows = [
                {'d': p['EndDate'][:10], 'n': round(float(p['Value']), 4)}
                for p in series if float(p.get('Value', 0)) > 1000
            ]
            if rows:
                print(f'Morningstar NAV (token={token}): {len(rows)} rows, last={rows[-1]}')
                return rows
        except Exception as e:
            print(f'Morningstar token={token} failed: {e}')

    return None


# ─── OSEBX ───────────────────────────────────────────────────────────────────

def fetch_euronext_osebx(from_date, last_known):
    """Euronext Live chart API — primary OSEBX source."""
    try:
        url = 'https://live.euronext.com/intraday_chart/getChartData/NO0007035327-XOSL/max'
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            print(f'Euronext OSEBX HTTP {r.status_code}')
            return {}
        data = r.json()
        if not isinstance(data, list):
            return {}
        cut_ms = int(datetime.strptime(from_date, '%Y-%m-%d').timestamp() * 1000) - 86400000
        map_ = {}
        for row in data:
            ts = row[0] if len(row) > 0 else None
            close = row[4] if len(row) > 4 else (row[1] if len(row) > 1 else None)
            if not ts or not close:
                continue
            if ts < cut_ms:
                continue
            val = round(float(close), 4)
            if not valid_osebx(val, last_known):
                continue
            # Oslo time UTC+2
            d = datetime.utcfromtimestamp(ts/1000 + 7200).strftime('%Y-%m-%d')
            map_[d] = val
        if map_:
            keys = sorted(map_.keys())
            print(f'Euronext OSEBX: {len(map_)} rows, last={keys[-1]}={map_[keys[-1]]}')
        return map_
    except Exception as e:
        print(f'Euronext OSEBX failed: {e}')
        return {}



    try:
        start = int(datetime.strptime(from_date, '%Y-%m-%d').timestamp())
        end   = int((datetime.now() + timedelta(days=2)).timestamp())
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/%5EOSEBX?interval=1d&period1={start}&period2={end}'
        r = requests.get(url, headers=HEADERS, timeout=12)
        if not r.ok:
            return {}
        d = r.json()
        result = d.get('chart', {}).get('result', [])
        if not result:
            return {}
        timestamps = result[0].get('timestamp', [])
        closes = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])
        osebx = {}
        for t, c in zip(timestamps, closes):
            if c is None:
                continue
            val = round(float(c), 4)
            if valid_osebx(val, last_known):
                d_str = (datetime.utcfromtimestamp(t) + timedelta(hours=1)).strftime('%Y-%m-%d')
                osebx[d_str] = val
        print(f'OSEBX: {len(osebx)} rows, last={list(osebx.items())[-3:] if osebx else []}')
        return osebx
    except Exception as e:
        print(f'OSEBX failed: {e}')
        return {}


# ─── xlsx bulk import ────────────────────────────────────────────────────────

def import_xlsx():
    print(f'Found {XLSX.name}, importing...')
    df = pd.read_excel(XLSX, sheet_name='Avkastning', header=None)
    data = df.iloc[6:].copy()
    data.columns = ['date', 'pan_nav', 'osefx']
    data = data.dropna(subset=['date', 'pan_nav'])
    data['date'] = pd.to_datetime(data['date'])
    data = data[data['pan_nav'].apply(lambda x: isinstance(x, (int, float)))]
    data = data[data['pan_nav'] > 0]
    data = data.sort_values('date')
    since = data[data['date'] >= '2021-12-20'].copy()
    rows = []
    for _, row in since.iterrows():
        o = round(float(row['osefx']), 4) if pd.notna(row['osefx']) and isinstance(row['osefx'], (int, float)) else None
        if o and (o < 100 or o > 5000):
            o = None
        rows.append({
            'd': row['date'].strftime('%Y-%m-%d'),
            'n': round(float(row['pan_nav']), 4),
            'o': o
        })
    print(f'xlsx: {len(rows)} rows, last={rows[-1]}')
    return rows


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    existing = load()
    existing_dates = {r['d'] for r in existing}

    # Bulk import from xlsx if present
    if XLSX.exists():
        existing = import_xlsx()
        existing_dates = {r['d'] for r in existing}

    from_date   = last_date(existing)
    last_nav    = existing[-1]['n'] if existing else None
    last_ose    = last_osebx(existing)

    print(f'Fetching from {from_date}, last NAV={last_nav}, last OSEBX={last_ose}')

    # ── Fetch NAV ──────────────────────────────────────
    new_rows = fetch_pareto(last_nav)

    if not new_rows:
        print('Pareto failed, trying Yahoo Finance...')
        new_rows = fetch_yahoo_nav(from_date, last_nav)

    if not new_rows:
        print('Yahoo failed, trying Morningstar...')
        new_rows = fetch_morningstar_nav(from_date)

    if not new_rows:
        print('All NAV sources failed. Saving existing data.')
        save(existing)
        return

    # ── Fetch OSEBX ────────────────────────────────────
    osebx_map = fetch_euronext_osebx(from_date, last_ose)
    if not osebx_map:
        print('Euronext OSEBX failed, trying Yahoo Finance...')
        osebx_map = fetch_osebx(from_date, last_ose)

    # ── Merge ──────────────────────────────────────────
    last_nav_date = new_rows[-1]['d']
    added = 0
    filled = 0
    for row in new_rows:
        if row['d'] not in existing_dates:
            existing.append({
                'd': row['d'],
                'n': row['n'],
                'o': osebx_map.get(row['d'])
            })
            existing_dates.add(row['d'])
            added += 1
        else:
            idx = next((i for i, r in enumerate(existing) if r['d'] == row['d']), None)
            if idx is not None:
                existing[idx]['n'] = row['n']
                if not existing[idx].get('o') and osebx_map.get(row['d']):
                    existing[idx]['o'] = osebx_map[row['d']]
                    filled += 1

    # Fill missing OSEBX only up to last NAV date
    for row in existing:
        if row['d'] > last_nav_date:
            break
        if not row.get('o') and osebx_map.get(row['d']):
            row['o'] = osebx_map[row['d']]
            filled += 1

    # Ensure last NAV entry has OSEBX — use closest available date if exact missing
    last_nav_entry = next((r for r in reversed(existing) if r['d'] == last_nav_date), None)
    if last_nav_entry and not last_nav_entry.get('o'):
        closest = max((d for d in osebx_map if d <= last_nav_date), default=None)
        if closest:
            last_nav_entry['o'] = osebx_map[closest]
            filled += 1
            print(f'Used closest OSEBX {closest}={osebx_map[closest]} for NAV date {last_nav_date}')

    save(existing)
    last = existing[-1]
    print(f'Done. Added={added}, OSEBX filled={filled}, Total={len(existing)}')
    print(f'Latest: {last["d"]} NAV={last["n"]} OSEBX={last.get("o")}')


if __name__ == '__main__':
    main()
