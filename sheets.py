"""
sheets.py — turn the MASTER sheet and the PRODUCTION sheet into database rows.

Both the 📥 Upload page and sheets_sync.py use this, so column mapping lives
in exactly one place. Sheet headers are matched case-insensitively and
ignoring spaces / punctuation; add any of her actual header names to the
lists below.
"""
from __future__ import annotations

import re
from datetime import datetime
import pandas as pd

# target column -> possible header names in her sheet (first match wins)
MASTER_COLUMNS = {
    'sku_id':       ['sku', 'sku code', 'sku id', 'seller sku', 'item sku', 'ean', 'barcode'],
    'style_code':   ['style', 'style code', 'style no', 'design no', 'design', 'article'],
    'product_name': ['product name', 'name', 'title', 'description', 'item name'],
    'category':     ['category', 'product type', 'type', 'sub category'],
    'gender':       ['gender', 'for'],
    'color':        ['color', 'colour'],
    'size':         ['size'],
    'mrp':          ['mrp', 'max retail price'],
    'cost_price':   ['cost', 'cost price', 'landed cost', 'cp', 'unit cost', 'total cost'],
    'launch_date':  ['launch date', 'launch', 'live date', 'listing date'],
}
MASTER_REQUIRED = ['sku_id']

PRODUCTION_COLUMNS = {
    'lot_id':        ['lot', 'lot no', 'lot id', 'po no', 'po number', 'order no', 'job no'],
    'style_code':    ['style', 'style code', 'style no', 'design no', 'design', 'article'],
    'product_name':  ['product name', 'name', 'description', 'item'],
    'category':      ['category', 'product type', 'type'],
    'vendor':        ['vendor', 'supplier', 'factory', 'job worker', 'karigar'],
    'color':         ['color', 'colour'],
    'planned_qty':   ['planned qty', 'qty', 'quantity', 'order qty', 'total qty', 'planned'],
    'received_qty':  ['received qty', 'received', 'inward qty', 'inward', 'delivered qty'],
    'po_date':       ['po date', 'date', 'order date', 'raised on', 'start date'],
    'expected_date': ['expected date', 'delivery date', 'due date', 'eta', 'expected'],
    'current_stage': ['stage', 'status', 'current stage', 'process'],
}
PRODUCTION_REQUIRED = ['lot_id', 'planned_qty']


def _norm(s) -> str:
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def map_columns(df: pd.DataFrame, spec: dict, required: list) -> tuple[pd.DataFrame, list[str]]:
    """Rename sheet columns to target names. Returns (frame, missing_required)."""
    norm_to_actual = {_norm(c): c for c in df.columns}
    rename = {}
    for target, candidates in spec.items():
        for cand in candidates:
            if _norm(cand) in norm_to_actual and norm_to_actual[_norm(cand)] not in rename:
                rename[norm_to_actual[_norm(cand)]] = target
                break
    out = df.rename(columns=rename)
    keep = [c for c in spec if c in out.columns]
    out = out[keep].copy()
    missing = [c for c in required if c not in out.columns]
    return out, missing


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors='coerce', dayfirst=True).dt.date


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r'[₹,\s]', '', regex=True), errors='coerce')


def clean_master(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out, missing = map_columns(df, MASTER_COLUMNS, MASTER_REQUIRED)
    if missing:
        return out, missing
    out['sku_id'] = out['sku_id'].astype(str).str.strip()
    out = out[out['sku_id'].ne('') & out['sku_id'].ne('nan')]
    for c in ('mrp', 'cost_price'):
        if c in out:
            out[c] = _to_num(out[c])
    if 'launch_date' in out:
        out['launch_date'] = _to_date(out['launch_date'])
    for c in ('style_code', 'product_name', 'category', 'gender', 'color', 'size'):
        if c in out:
            out[c] = out[c].astype(str).str.strip().replace({'nan': None, '': None})
    out = out.drop_duplicates('sku_id', keep='last')
    return out, []


STAGE_WORDS = {
    'planning': ['plan', 'pending', 'not started', 'new'],
    'fabric':   ['fabric', 'material', 'sourcing'],
    'cutting':  ['cut'],
    'stitching': ['stitch', 'sewing', 'production', 'in process', 'wip'],
    'finishing': ['finish', 'wash', 'iron', 'packing', 'qc', 'ready'],
    'received': ['received', 'inward', 'complete', 'done', 'delivered', 'closed'],
}


def normalise_stage(value) -> str:
    v = str(value or '').lower()
    for stage, words in STAGE_WORDS.items():
        if any(w in v for w in words):
            return stage
    return 'planning'


def clean_production(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out, missing = map_columns(df, PRODUCTION_COLUMNS, PRODUCTION_REQUIRED)
    if missing:
        return out, missing
    out['lot_id'] = out['lot_id'].astype(str).str.strip()
    out = out[out['lot_id'].ne('') & out['lot_id'].ne('nan')]
    out['planned_qty'] = _to_num(out['planned_qty']).fillna(0).astype(int)
    out['received_qty'] = _to_num(out['received_qty']).fillna(0).astype(int) if 'received_qty' in out else 0
    for c in ('po_date', 'expected_date'):
        if c in out:
            out[c] = _to_date(out[c])
    out['current_stage'] = out['current_stage'].map(normalise_stage) if 'current_stage' in out else 'planning'
    # a lot with everything received is 'received' whatever the sheet says
    out.loc[(out['received_qty'] >= out['planned_qty']) & (out['planned_qty'] > 0), 'current_stage'] = 'received'
    for c in ('style_code', 'product_name', 'category', 'vendor', 'color'):
        if c in out:
            out[c] = out[c].astype(str).str.strip().replace({'nan': None, '': None})
    out['updated_at'] = datetime.now()
    out = out.drop_duplicates('lot_id', keep='last')
    return out, []


# ---------------------------------------------------------------- writers
def _rows(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    d = df.reindex(columns=cols)
    d = d.astype(object).where(pd.notna(d), None)
    return [tuple(r) for r in d.itertuples(index=False, name=None)]


def write_master(conn, df: pd.DataFrame) -> int:
    from psycopg2.extras import execute_values
    cols = ['sku_id', 'style_code', 'product_name', 'category', 'gender', 'color', 'size',
            'mrp', 'cost_price', 'launch_date']
    rows = _rows(df, cols)
    with conn.cursor() as cur:
        execute_values(cur, f"""
            INSERT INTO dim_sku ({', '.join(cols)}) VALUES %s
            ON CONFLICT (sku_id) DO UPDATE SET
              {', '.join(f'{c} = COALESCE(EXCLUDED.{c}, dim_sku.{c})' for c in cols[1:])},
              updated_at = now()
        """, rows, page_size=1000)
        cur.execute("INSERT INTO sync_log (source, rows_written, status) VALUES ('master_sheet', %s, 'ok')", (len(rows),))
    conn.commit()
    return len(rows)


def write_production(conn, df: pd.DataFrame, replace: bool = True) -> int:
    from psycopg2.extras import execute_values
    cols = ['lot_id', 'style_code', 'product_name', 'category', 'vendor', 'color', 'planned_qty',
            'received_qty', 'po_date', 'expected_date', 'current_stage', 'updated_at']
    rows = _rows(df, cols)
    with conn.cursor() as cur:
        if replace:
            cur.execute('DELETE FROM fact_production_lot')  # the sheet is the whole truth
        execute_values(cur, f"""
            INSERT INTO fact_production_lot ({', '.join(cols)}) VALUES %s
            ON CONFLICT (lot_id) DO UPDATE SET
              {', '.join(f'{c} = EXCLUDED.{c}' for c in cols[1:])}
        """, rows, page_size=1000)
        cur.execute("INSERT INTO sync_log (source, rows_written, status) VALUES ('production_sheet', %s, 'ok')", (len(rows),))
    conn.commit()
    return len(rows)
