"""
data.py — the ONLY place pages get data from.

Three sources, in order:
    supabase   SUPABASE_HOST is set in secrets           -> the real thing
    local      files uploaded through the Data Hub        -> renders today, single machine
    demo       neither                                    -> generated sample data

Every loader returns the same columns whatever the source, so no page has to
know which one is live.
"""
from __future__ import annotations

from datetime import date
import pandas as pd
import streamlit as st

import db
import demo_data
import store

TTL = 300  # seconds

SALES_COLUMNS = ['sale_date', 'channel_id', 'channel_name', 'order_id', 'invoice_no', 'sku_id',
                 'product_name', 'category', 'gender', 'color', 'qty', 'net_value', 'discount',
                 'gross_value', 'taxable_value', 'tax_value', 'city', 'state', 'pincode',
                 'payment_method', 'warehouse_id', 'return_flag', 'style_code', 'size',
                 'mrp', 'cost_price']
STOCK_COLUMNS = ['snapshot_date', 'sku_id', 'warehouse_id', 'warehouse_name', 'stock_qty',
                 'style_code', 'product_name', 'category', 'gender', 'color', 'size', 'mrp', 'cost_price']
PRODUCTION_COLUMNS = ['lot_id', 'style_code', 'product_name', 'category', 'vendor', 'color',
                      'planned_qty', 'received_qty', 'po_date', 'expected_date', 'current_stage', 'updated_at']


def source() -> str:
    if db.is_configured():
        return 'supabase'
    if store.has_any():
        return 'local'
    return 'demo'


BOOL_COLUMNS = ('return_flag', 'is_active', 'is_freebie')
INT_COLUMNS = ('qty', 'stock_qty', 'planned_qty', 'received_qty')
FLOAT_COLUMNS = ('gross_value', 'discount', 'net_value', 'taxable_value', 'tax_value',
                 'mrp', 'cost_price', 'deduction_pct', 'overhead_per_unit')


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    """
    Force real dtypes on the columns pages do arithmetic and masking with.

    A query that returns NO rows hands back every column as dtype object.
    `~df['return_flag']` on an object column then yields an object Series, and
    `df[object_series]` is read by pandas as a LIST OF COLUMN NAMES rather than
    a boolean mask — so it silently returns a frame with zero columns and the
    next lookup dies with a confusing KeyError naming a column that is right
    there. This bit the Sales page the moment a period had no sales in it.

    Coercing here also absorbs the Decimals and Nones psycopg2 returns for
    numeric columns, so no page has to think about it.
    """
    for c in BOOL_COLUMNS:
        if c in df.columns:
            df[c] = df[c].map(lambda v: bool(v) if v is not None and v is not pd.NA else False).astype(bool)
    for c in INT_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype('int64')
    for c in FLOAT_COLUMNS:
        if c in df.columns:
            # cost_price stays NaN when absent — has_costs() depends on telling
            # "no cost loaded" apart from "costs zero".
            df[c] = pd.to_numeric(df[c], errors='coerce').astype('float64')
    return df


def _empty(cols: list[str]) -> pd.DataFrame:
    return _coerce(pd.DataFrame({c: pd.Series(dtype='object') for c in cols}))


# ---------------------------------------------------------------- reference
@st.cache_data(ttl=TTL, show_spinner=False)
def load_skus() -> pd.DataFrame:
    s = source()
    if s == 'demo':
        return demo_data.skus()
    if s == 'local':
        df = store.load('sku')
        return df if df is not None else _empty(['sku_id', 'style_code', 'product_name', 'category',
                                                 'gender', 'color', 'size', 'mrp', 'cost_price'])
    df = db.query_df("""
        SELECT sku_id, style_code, product_name, category, gender, color, size,
               mrp::float AS mrp, cost_price::float AS cost_price, launch_date, is_active
        FROM dim_sku
    """)
    for c in ('style_code', 'product_name', 'category', 'gender', 'color', 'size'):
        if c in df:
            df[c] = df[c].fillna('')
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def load_channels() -> pd.DataFrame:
    s = source()
    if s == 'demo':
        return demo_data.channels()
    if s == 'local':
        sales = store.load('sales')
        known = dict(zip(demo_data.CHANNELS['channel_id'], demo_data.CHANNELS['deduction_pct']))
        if sales is None or sales.empty:
            return demo_data.channels()
        ch = sales[['channel_id', 'channel_name']].drop_duplicates().sort_values('channel_name')
        ch['deduction_pct'] = ch['channel_id'].map(known).fillna(30.0)
        ch['overhead_per_unit'] = 40.0
        return ch.reset_index(drop=True)
    return db.query_df("""
        SELECT channel_id, channel_name, deduction_pct::float AS deduction_pct,
               overhead_per_unit::float AS overhead_per_unit
        FROM dim_channel ORDER BY channel_name
    """)


@st.cache_data(ttl=TTL, show_spinner=False)
def load_warehouses() -> pd.DataFrame:
    s = source()
    if s == 'demo':
        return demo_data.warehouses()
    if s == 'local':
        stock = store.load('stock')
        if stock is None or stock.empty:
            return _empty(['warehouse_id', 'warehouse_name', 'uc_facility_code'])
        wh = stock[['warehouse_id']].drop_duplicates()
        wh['warehouse_name'] = wh['warehouse_id']
        wh['uc_facility_code'] = wh['warehouse_id']
        return wh
    return db.query_df('SELECT warehouse_id, warehouse_name, uc_facility_code FROM dim_warehouse')


# ---------------------------------------------------------------- facts
def _attach_sku(df: pd.DataFrame, want: list[str]) -> pd.DataFrame:
    """Add SKU attributes that are not already on the rows."""
    sku = load_skus()
    if sku is None or sku.empty:
        for c in want:
            if c not in df:
                df[c] = pd.NA
        return df
    missing = [c for c in want if c not in df.columns and c in sku.columns]
    if missing:
        df = df.merge(sku[['sku_id'] + missing], on='sku_id', how='left')
    for c in want:
        if c not in df:
            df[c] = pd.NA
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def load_sales(start: date, end: date) -> pd.DataFrame:
    s = source()
    if s == 'demo':
        df = demo_data.sales(240)
    elif s == 'local':
        df = store.load('sales')
        if df is None or df.empty:
            return _empty(SALES_COLUMNS)
    else:
        df = db.query_df("""
            SELECT sale_date, channel_id, warehouse_id, order_id, invoice_no, sku_id, qty,
                   gross_value::float AS gross_value, discount::float AS discount,
                   net_value::float AS net_value, taxable_value::float AS taxable_value,
                   tax_value::float AS tax_value, city, state, pincode, payment_method, return_flag
            FROM fact_sales WHERE sale_date BETWEEN %s AND %s
        """, (start, end))
    df = df.copy()
    df['sale_date'] = pd.to_datetime(df['sale_date'])
    df = df[(df['sale_date'] >= pd.Timestamp(start)) & (df['sale_date'] <= pd.Timestamp(end))]

    if 'channel_name' not in df.columns:
        ch = load_channels()[['channel_id', 'channel_name']]
        df = df.merge(ch, on='channel_id', how='left')
        df['channel_name'] = df['channel_name'].fillna(df['channel_id'])
    df = _attach_sku(df, ['style_code', 'product_name', 'category', 'gender', 'color', 'size',
                          'mrp', 'cost_price'])
    df['style_code'] = df['style_code'].fillna(df['sku_id'])
    df['category'] = df['category'].fillna('Uncategorised')
    for c in SALES_COLUMNS:
        if c not in df:
            df[c] = pd.NA
    return _coerce(df)


@st.cache_data(ttl=TTL, show_spinner=False)
def load_stock_latest() -> pd.DataFrame:
    s = source()
    if s == 'demo':
        df = demo_data.stock()
    elif s == 'local':
        df = store.load('stock')
        if df is None or df.empty:
            return _empty(STOCK_COLUMNS)
    else:
        df = db.query_df("""
            SELECT s.snapshot_date, s.sku_id, s.warehouse_id, s.stock_qty
            FROM fact_stock_snapshot s
            JOIN (SELECT warehouse_id, MAX(snapshot_date) AS d
                  FROM fact_stock_snapshot GROUP BY warehouse_id) l
              ON l.warehouse_id = s.warehouse_id AND l.d = s.snapshot_date
        """)
    df = df.copy()
    df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
    if 'warehouse_name' not in df.columns:
        wh = load_warehouses()
        if not wh.empty:
            df = df.merge(wh[['warehouse_id', 'warehouse_name']], on='warehouse_id', how='left')
        else:
            df['warehouse_name'] = df['warehouse_id']
        df['warehouse_name'] = df['warehouse_name'].fillna(df['warehouse_id'])
    df = _attach_sku(df, ['style_code', 'product_name', 'category', 'gender', 'color', 'size',
                          'mrp', 'cost_price'])
    df['style_code'] = df['style_code'].fillna(df['sku_id'])
    df['category'] = df['category'].fillna('Uncategorised')
    return _coerce(df)


@st.cache_data(ttl=TTL, show_spinner=False)
def load_production() -> pd.DataFrame:
    s = source()
    if s == 'demo':
        return demo_data.production()
    if s == 'local':
        df = store.load('production')
        return df if df is not None else _empty(PRODUCTION_COLUMNS)
    df = db.query_df("""
        SELECT lot_id, style_code, product_name, category, vendor, color,
               planned_qty, received_qty, po_date, expected_date, current_stage, updated_at
        FROM fact_production_lot
    """)
    for c in ('po_date', 'expected_date', 'updated_at'):
        df[c] = pd.to_datetime(df[c])
    return _coerce(df)


@st.cache_data(ttl=60, show_spinner=False)
def load_sync_log() -> pd.DataFrame:
    s = source()
    if s == 'supabase':
        return db.query_df("""
            SELECT DISTINCT ON (source) source, ran_at, rows_written, status, message
            FROM sync_log ORDER BY source, ran_at DESC
        """)
    if s == 'local':
        meta = store.read_meta()
        return pd.DataFrame([{'source': k, 'ran_at': v.get('saved_at'), 'rows_written': v.get('rows'),
                              'status': 'ok', 'message': v.get('note', '')} for k, v in meta.items()])
    return pd.DataFrame(columns=['source', 'ran_at', 'rows_written', 'status', 'message'])


@st.cache_data(ttl=TTL, show_spinner=False)
def date_bounds() -> tuple:
    """Earliest and latest sale date that exists, or (None, None)."""
    s = source()
    if s == 'demo':
        from datetime import date as _d, timedelta as _td
        return _d.today() - _td(days=240), _d.today()
    if s == 'local':
        df = store.load('sales')
        if df is None or df.empty:
            return None, None
        d = pd.to_datetime(df['sale_date'])
        return d.min().date(), d.max().date()
    df = db.query_df('SELECT MIN(sale_date) a, MAX(sale_date) b FROM fact_sales')
    if df.empty or pd.isna(df.iloc[0]['a']):
        return None, None
    return pd.Timestamp(df.iloc[0]['a']).date(), pd.Timestamp(df.iloc[0]['b']).date()


def has_costs() -> bool:
    """True when at least some SKUs carry a cost price (i.e. the Master sheet is loaded)."""
    sku = load_skus()
    return not sku.empty and 'cost_price' in sku and pd.to_numeric(sku['cost_price'], errors='coerce').notna().any()


def clear_cache() -> None:
    st.cache_data.clear()
