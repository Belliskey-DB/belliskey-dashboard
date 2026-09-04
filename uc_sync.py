"""
uc_sync.py — Unicommerce → Supabase (sales + stock). Run daily.

    python uc_sync.py                 # yesterday+today's orders, full stock snapshot
    python uc_sync.py --days 90       # backfill 90 days of orders
    python uc_sync.py --no-stock      # orders only

Reads config from environment variables OR .streamlit/secrets.toml:
    UC_TENANT, UC_USERNAME, UC_PASSWORD        (Unicommerce API user — ask Unicommerce support to enable API access)
    SUPABASE_HOST, SUPABASE_PORT, SUPABASE_USER, SUPABASE_PASSWORD, SUPABASE_DB

Mapping lives in the DATABASE, not here:
    dim_warehouse.uc_facility_code   -> which facility is which warehouse
    dim_channel.uc_channel_codes     -> which UC channel codes belong to which marketplace
Orders on an unmapped channel are recorded under CH_OTHER and reported at the end,
so nothing is silently dropped.

Stock is always a FULL snapshot per facility (UC's "changed since" mode drops
SKUs that did not move, which makes stable stock vanish).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import psycopg2
from psycopg2.extras import execute_values

IST = ZoneInfo('Asia/Kolkata')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('uc_sync')


# ---------------------------------------------------------------- config
def _load_secrets_file() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.streamlit', 'secrets.toml')
    if not os.path.exists(path):
        return {}
    try:
        import tomllib  # py3.11+
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except ImportError:
        out = {}
        for line in open(path):
            if '=' in line and not line.strip().startswith('#') and not line.strip().startswith('['):
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out


_SECRETS = _load_secrets_file()


def cfg(key: str, default=None):
    return os.environ.get(key) or _SECRETS.get(key, default)


UC_TENANT = cfg('UC_TENANT', '')
UC_BASE = f'https://{UC_TENANT}.unicommerce.com'


def db_conn():
    return psycopg2.connect(
        host=cfg('SUPABASE_HOST'), port=int(cfg('SUPABASE_PORT', 5432)),
        user=cfg('SUPABASE_USER', 'postgres'), password=cfg('SUPABASE_PASSWORD'),
        database=cfg('SUPABASE_DB', 'postgres'), sslmode='require', connect_timeout=15,
    )


def load_maps(conn) -> tuple[dict, dict, set]:
    with conn.cursor() as cur:
        cur.execute('SELECT uc_facility_code, warehouse_id FROM dim_warehouse WHERE uc_facility_code IS NOT NULL')
        fac = {str(f).strip().upper(): w for f, w in cur.fetchall()}
        cur.execute('SELECT channel_id, uc_channel_codes FROM dim_channel')
        ch = {}
        for cid, codes in cur.fetchall():
            for c in (codes or []):
                ch[str(c).strip().upper()] = cid
        cur.execute('SELECT sku_id FROM dim_sku')
        skus = {str(r[0]) for r in cur.fetchall()}
    return fac, ch, skus


# ---------------------------------------------------------------- UC API
def uc_token() -> str:
    u, p = cfg('UC_USERNAME'), cfg('UC_PASSWORD')
    if not (UC_TENANT and u and p):
        raise SystemExit('Set UC_TENANT, UC_USERNAME and UC_PASSWORD (env or .streamlit/secrets.toml).')
    r = requests.get(f'{UC_BASE}/oauth/token', params={
        'grant_type': 'password', 'client_id': 'my-trusted-client', 'username': u, 'password': p}, timeout=30)
    r.raise_for_status()
    return r.json()['access_token']


def _hdr(token, facility=None):
    h = {'Content-Type': 'application/json', 'Authorization': f'bearer {token}'}
    if facility:
        h['Facility'] = facility
    return h


def uc_inventory(token, facility, skus: list) -> list[tuple[str, int]]:
    out = []
    for i in range(0, len(skus), 10000):
        r = requests.post(f'{UC_BASE}/services/rest/v1/inventory/inventorySnapshot/get',
                          headers=_hdr(token, facility), json={'itemTypeSKUs': skus[i:i + 10000]}, timeout=180)
        r.raise_for_status()
        for s in r.json().get('inventorySnapshots', []) or []:
            out.append((str(s.get('itemTypeSKU', '')).strip(), int(s.get('inventory', 0) or 0)))
    return out


def uc_search_orders(token, d0: date, d1: date):
    start, page = 0, 200
    while True:
        body = {'fromDate': f'{d0.isoformat()}T00:00:00.000Z', 'toDate': f'{d1.isoformat()}T23:59:59.999Z',
                'dateType': 'CREATED',
                'searchOptions': {'displayLength': page, 'displayStart': start, 'getCount': True}}
        r = requests.post(f'{UC_BASE}/services/rest/v1/oms/saleOrder/search', headers=_hdr(token), json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data.get('successful', True):
            raise RuntimeError(data.get('message', 'saleOrder/search failed'))
        els = data.get('elements', []) or []
        yield from els
        start += len(els)
        if not els or start >= int(data.get('totalRecords', 0) or 0):
            break


def uc_get_order(token, code):
    r = requests.post(f'{UC_BASE}/services/rest/v1/oms/saleorder/get', headers=_hdr(token), json={'code': code}, timeout=60)
    r.raise_for_status()
    return r.json().get('saleOrderDTO') or {}


# ---------------------------------------------------------------- parse
def parse_order(o: dict, fac_map: dict, ch_map: dict, batch: str, unmapped: set) -> list[tuple]:
    rows = []
    order_id = o.get('displayOrderCode') or o.get('code')
    uc_ch = str(o.get('channel') or 'UNKNOWN').upper()
    channel_id = ch_map.get(uc_ch)
    if not channel_id:
        unmapped.add(uc_ch)
        channel_id = 'CH_OTHER'
    created = o.get('created')
    try:
        sale_date = datetime.fromisoformat(str(created).replace('Z', '+00:00')).astimezone(IST).date()
    except Exception:
        sale_date = datetime.now(IST).date()

    # UC returns one item per PIECE; group by SKU + status so qty adds up.
    grouped: dict[tuple, dict] = {}
    for it in o.get('saleOrderItems', []) or []:
        status = str(it.get('statusCode') or '').upper()
        if status in ('CANCELLED', 'CANCELED', 'UNFULFILLABLE'):
            continue
        sku = str(it.get('itemSKU') or '').strip()
        if not sku:
            continue
        fac = str(it.get('facilityCode') or '').strip().upper()
        wh = fac_map.get(fac)
        key = (sku, wh)
        g = grouped.setdefault(key, {'qty': 0, 'gross': 0.0, 'disc': 0.0, 'ret_qty': 0, 'ret_gross': 0.0, 'ret_disc': 0.0})
        sp = float(it.get('sellingPrice') or 0)
        disc = float(it.get('discount') or 0)
        g['qty'] += 1
        g['gross'] += sp
        g['disc'] += disc
        if status in ('RETURNED', 'RETURN_EXPECTED', 'CUSTOMER_RETURNED'):
            g['ret_qty'] += 1
            g['ret_gross'] += sp
            g['ret_disc'] += disc
    for (sku, wh), g in grouped.items():
        net = max(0.0, g['gross'] - g['disc'])
        rows.append((sale_date, channel_id, wh, order_id, sku, g['qty'], g['gross'], g['disc'], net, False, batch))
        if g['ret_qty']:
            rnet = max(0.0, g['ret_gross'] - g['ret_disc'])
            rows.append((sale_date, channel_id, wh, order_id, sku, g['ret_qty'], g['ret_gross'], g['ret_disc'], rnet, True, batch))
    return rows


# ---------------------------------------------------------------- write
def write_sales(conn, rows, order_ids):
    with conn.cursor() as cur:
        cur.execute('DELETE FROM fact_sales WHERE order_id = ANY(%s)', (list(set(order_ids)),))
        if rows:
            execute_values(cur, """INSERT INTO fact_sales (sale_date, channel_id, warehouse_id, order_id, sku_id, qty,
                gross_value, discount, net_value, return_flag, ingest_batch_id) VALUES %s
                ON CONFLICT (order_id, sku_id, return_flag) DO UPDATE SET qty = EXCLUDED.qty,
                gross_value = EXCLUDED.gross_value, discount = EXCLUDED.discount, net_value = EXCLUDED.net_value""",
                rows, page_size=1000)
    conn.commit()


def write_stock(conn, snapshot_date, warehouse_id, rows):
    with conn.cursor() as cur:
        cur.execute('DELETE FROM fact_stock_snapshot WHERE snapshot_date = %s AND warehouse_id = %s', (snapshot_date, warehouse_id))
        execute_values(cur, 'INSERT INTO fact_stock_snapshot (snapshot_date, sku_id, warehouse_id, stock_qty) VALUES %s',
                       [(snapshot_date, sku, warehouse_id, qty) for sku, qty in rows], page_size=1000)
    conn.commit()


def log_run(conn, source, n, status, msg=''):
    with conn.cursor() as cur:
        cur.execute('INSERT INTO sync_log (source, rows_written, status, message) VALUES (%s,%s,%s,%s)', (source, n, status, msg[:500]))
    conn.commit()


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2, help='how many days back to pull orders (default 2)')
    ap.add_argument('--no-stock', action='store_true')
    ap.add_argument('--no-sales', action='store_true')
    a = ap.parse_args()

    conn = db_conn()
    fac_map, ch_map, skus = load_maps(conn)
    if not fac_map:
        raise SystemExit('dim_warehouse has no uc_facility_code rows — add the facilities first (see README).')
    token = uc_token()
    today = datetime.now(IST).date()

    if not a.no_sales:
        d0 = today - timedelta(days=a.days - 1)
        batch = f'uc_{datetime.now(IST):%Y%m%d_%H%M}'
        unmapped: set = set()
        try:
            codes = [e.get('code') for e in uc_search_orders(token, d0, today) if e.get('code')]
            log.info(f'{len(codes)} orders between {d0} and {today}')
            rows, ids = [], []
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = {ex.submit(uc_get_order, token, c): c for c in codes}
                for i, f in enumerate(as_completed(futs), 1):
                    o = f.result()
                    if o:
                        rows += parse_order(o, fac_map, ch_map, batch, unmapped)
                        ids.append(o.get('displayOrderCode') or o.get('code'))
                    if i % 200 == 0:
                        log.info(f'  fetched {i}/{len(codes)}')
            write_sales(conn, rows, ids)
            unk = [s for s in {r[4] for r in rows} if s not in skus]
            msg = f'unmapped channels: {sorted(unmapped)}' if unmapped else ''
            if unk:
                msg += f' | {len(unk)} SKUs not in dim_sku (e.g. {unk[:5]})'
            log_run(conn, 'unicommerce_sales', len(rows), 'ok', msg)
            log.info(f'sales: {len(rows)} rows written. {msg}')
        except Exception as e:
            log_run(conn, 'unicommerce_sales', 0, 'error', str(e))
            raise

    if not a.no_stock:
        sku_list = sorted(skus)
        if not sku_list:
            log.warning('dim_sku is empty — load the Master sheet first; stock needs the SKU list.')
        for fac, wh in fac_map.items():
            try:
                rows = uc_inventory(token, fac, sku_list)
                write_stock(conn, today, wh, rows)
                log_run(conn, 'unicommerce_stock', len(rows), 'ok', f'{fac} -> {wh}')
                log.info(f'stock {fac}: {len(rows)} SKUs')
            except Exception as e:
                log_run(conn, 'unicommerce_stock', 0, 'error', f'{fac}: {e}')
                log.error(f'stock {fac} failed: {e}')
    conn.close()


if __name__ == '__main__':
    main()
