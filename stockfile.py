"""
stockfile.py — read Belliskey's own stock workbook (STOCK FILE NEW.xlsm).

Two sheets matter:

  Inventory            one row per barcode: style, product type, colour, size,
                       MRP, current stock. This is the closest thing to a SKU
                       master she has, and it is the only place style code and
                       size exist — the Unicommerce sales export has neither.

  Transaction History  every dispatch and return movement, by barcode and date.
                       Units only: no value and no marketplace, so it cannot
                       replace the returns export, but it does say what came
                       back and when.

Also reads the Uniware "Inventory Adjustment" template, which is generated from
this same workbook and carries the same numbers in `Product Code*` / `Quantity*`.
"""
from __future__ import annotations

import re
from datetime import date
import pandas as pd

INVENTORY_SHEET = 'Inventory'
HISTORY_SHEET = 'Transaction History'

# 'Women's Jeans' -> ('Jeans', 'Women'). Her sheet carries the gender inside the
# product type, and splitting them lets the dashboard filter on either.
PRODUCT_RE = re.compile(r"^\s*(men|women|unisex|kids|boys|girls)\s*(?:'s|s')?\s*(.*)$", re.I)


def normalise_product(raw) -> tuple[str, str]:
    """Return (category, gender). Fixes casing so 'skirt' and 'Skirt' are one thing."""
    s = re.sub(r'\s+', ' ', str(raw or '')).strip()
    if not s:
        return '', ''
    m = PRODUCT_RE.match(s)
    if m:
        gender = m.group(1).title()
        gender = {'Boys': 'Boys', 'Girls': 'Girls', 'Kids': 'Kids'}.get(gender, gender)
        category = m.group(2).strip()
    else:
        gender, category = '', s
    return CANONICAL.get(_key(category), category.title()), gender


def _key(s: str) -> str:
    """Case- and punctuation-insensitive lookup key: \"Women's skirt\" -> womensskirt."""
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


# Her sheet spells the same product several ways — 'skirt', 'Skirt' and 'Skirts'
# are three entries for one category, which would show as three bars on a chart.
CANONICAL = {k: v for v, keys in {
    'Jeans':      ['jeans', 'jean'],
    'Shorts':     ['shorts', 'short'],
    'T-Shirt':    ['tshirt', 'tshirts', 'tee', 'tees'],
    'Skirt':      ['skirt', 'skirts'],
    'Skort':      ['skort', 'skorts'],
    'Top':        ['top', 'tops'],
    'Jacket':     ['jacket', 'jackets'],
    'Dress':      ['dress', 'dresses'],
    'Co-ord Set': ['set', 'sets', 'coord', 'coordset', 'coords'],
    'Joggers':    ['joggers', 'jogger'],
    'Dungaree':   ['dungaree', 'dungarees'],
    'Sweatshirt': ['sweatshirt', 'sweatshirts'],
    'Shirt':      ['shirt', 'shirts'],
    'Cargo Pants': ['cargopant', 'cargopants'],
    'Trousers':   ['trouser', 'trousers', 'pant', 'pants'],
}.items() for k in keys}


def _read(file, sheet: str, header: int) -> pd.DataFrame:
    df = pd.read_excel(file, sheet_name=sheet, header=header, dtype=str)
    df = df.dropna(axis=1, how='all')
    df.columns = [str(c).strip() for c in df.columns]
    return df


def sheet_names(file) -> list[str]:
    try:
        return pd.ExcelFile(file).sheet_names
    except Exception:
        return []


def parse_inventory(file, snapshot_date: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (sku_master, stock_snapshot, report)."""
    report: dict = {}
    try:
        df = _read(file, INVENTORY_SHEET, 0)
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), {'error': f'Could not read the "{INVENTORY_SHEET}" sheet: {e}'}

    need = {'SKU (EAN)', 'Style No.', 'Product', 'Colour', 'Size', 'MRP', 'Current Stock'}
    have = set(df.columns)
    if not need <= have:
        return pd.DataFrame(), pd.DataFrame(), {
            'error': f'The Inventory sheet is missing {", ".join(sorted(need - have))}. '
                     f'Columns found: {", ".join(df.columns)}'}

    df['sku_id'] = df['SKU (EAN)'].astype(str).str.strip()
    df = df[df['sku_id'].ne('') & df['sku_id'].ne('nan') & ~df['sku_id'].str.lower().eq('none')]

    cat_gender = df['Product'].map(normalise_product)
    sku = pd.DataFrame({
        'sku_id': df['sku_id'],
        'style_code': df['Style No.'].astype(str).str.strip().replace({'nan': None}),
        'product_name': df['Product'].astype(str).str.strip().replace({'nan': None}),
        'category': [c for c, _ in cat_gender],
        'gender': [g for _, g in cat_gender],
        'color': df['Colour'].astype(str).str.strip().replace({'nan': None}),
        'size': df['Size'].astype(str).str.strip().replace({'nan': None}),
        'mrp': pd.to_numeric(df['MRP'], errors='coerce'),
        'cost_price': pd.NA,          # never in this workbook — comes from the purchase ledger
        'launch_date': pd.NaT,
    }).drop_duplicates('sku_id', keep='last')

    qty = pd.to_numeric(df['Current Stock'], errors='coerce').fillna(0).round().astype(int)
    stock = pd.DataFrame({
        'snapshot_date': pd.Timestamp(snapshot_date or date.today()),
        'sku_id': df['sku_id'],
        'warehouse_id': 'MAIN',
        'stock_qty': qty.clip(lower=0),
    }).drop_duplicates(['sku_id', 'warehouse_id'], keep='last')

    report.update(
        skus=len(sku), styles=int(sku['style_code'].nunique()),
        units=int(stock['stock_qty'].sum()),
        with_mrp=int(sku['mrp'].notna().sum()),
        out_of_stock=int((stock['stock_qty'] == 0).sum()),
        categories=sku['category'].value_counts().to_dict(),
        stock_value_at_mrp=float((sku.set_index('sku_id')['mrp'].reindex(stock['sku_id']).fillna(0).to_numpy()
                                  * stock['stock_qty'].to_numpy()).sum()),
    )
    return sku, stock, report


def parse_history(file) -> tuple[pd.DataFrame, dict]:
    """Dispatch and return movements. Units only — no value, no marketplace."""
    try:
        df = _read(file, HISTORY_SHEET, 3)
    except Exception as e:
        return pd.DataFrame(), {'error': f'Could not read "{HISTORY_SHEET}": {e}'}
    if 'Transaction ID' not in df.columns:
        return pd.DataFrame(), {'error': 'No "Transaction ID" column on the Transaction History sheet.'}
    df = df[df['Transaction ID'].notna()]
    out = pd.DataFrame({
        'txn_id': df['Transaction ID'].astype(str).str.strip(),
        'txn_date': pd.to_datetime(df['Date'], format='%d-%m-%Y', errors='coerce'),
        'sku_id': df['SKU (EAN)'].astype(str).str.strip(),
        'style_code': df.get('Style Number', pd.Series(dtype=str)).astype(str).str.strip(),
        'qty_changed': pd.to_numeric(df['Qty Changed'], errors='coerce').fillna(0).astype(int),
        'txn_type': df['Type'].astype(str).str.strip().str.title(),
    })
    out = out[out['sku_id'].ne('') & out['txn_date'].notna()]
    rep = {'rows': len(out), 'types': out['txn_type'].value_counts().to_dict(),
           'date_min': out['txn_date'].min(), 'date_max': out['txn_date'].max(),
           'dispatched': int(-out.loc[out.qty_changed < 0, 'qty_changed'].sum()),
           'returned': int(out.loc[out.qty_changed > 0, 'qty_changed'].sum())}
    return out.reset_index(drop=True), rep


def parse_uniware_adjustment(file, snapshot_date: date | None = None) -> tuple[pd.DataFrame, dict]:
    """The Uniware Inventory Adjustment template: Product Code* / Quantity*."""
    name = getattr(file, 'name', str(file)).lower()
    df = pd.read_excel(file, dtype=str) if name.endswith(('.xlsx', '.xls', '.xlsm')) \
        else pd.read_csv(file, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    code = next((c for c in df.columns if c.lower().startswith('product code')), None)
    qty = next((c for c in df.columns if c.lower().startswith('quantity')), None)
    if not code or not qty:
        return pd.DataFrame(), {'error': 'Expected columns "Product Code*" and "Quantity*". '
                                         f'Found: {", ".join(df.columns)}'}
    out = pd.DataFrame({
        'snapshot_date': pd.Timestamp(snapshot_date or date.today()),
        'sku_id': df[code].astype(str).str.strip(),
        'warehouse_id': 'MAIN',
        'stock_qty': pd.to_numeric(df[qty], errors='coerce').fillna(0).round().astype(int).clip(lower=0),
    })
    out = out[out['sku_id'].ne('') & out['sku_id'].ne('nan')].drop_duplicates(['sku_id', 'warehouse_id'], keep='last')
    return out.reset_index(drop=True), {'rows': len(out), 'units': int(out['stock_qty'].sum()),
                                        'zero_rows': int((out['stock_qty'] == 0).sum())}
