"""
unicommerce.py — read the Unicommerce "Tally GST Report" export (sales or returns).

This is the report Belliskey already exports:
    Unicommerce -> Reports -> Tally GST Report        (sales invoices)
    Unicommerce -> Reports -> Tally Return GST Report (credit notes / returns)

Both files have the same shape, so one parser handles both. Pass is_return=True
for the return export; those rows are stored with return_flag = True.

WHAT THE MONEY COLUMNS MEAN (checked against Belliskey's Jun-Aug file)
    Total            what the customer paid for the line, including GST
    Sales            the same line excluding GST (taxable value)
    CGST/SGST/IGST   the tax on it
    Discount Amount  the discount off MRP, NOT a deduction from Total.
                     Total + Discount Amount = MRP value of the line.

So we store:
    net_value      = Total            <- the revenue figure every page uses
    gross_value    = Total + Discount <- value at MRP
    discount       = Discount Amount
    taxable_value  = Sales
    tax_value      = CGST + SGST + IGST + UTGST + CESS

CUSTOMER DATA IS DROPPED. Names, addresses, phone numbers and AWB numbers are
never read into the dashboard. City, state and pincode are kept because they
drive the geography view and identify no one on their own.
"""
from __future__ import annotations

import re
import pandas as pd

# Unicommerce channel ledger -> (channel_id, display name)
CHANNEL_MAP = {
    'MYNTRAPPMP': ('CH_MYNTRA', 'Myntra'),
    'MYNTRA_PPMP': ('CH_MYNTRA', 'Myntra'),
    'MYNTRA': ('CH_MYNTRA', 'Myntra'),
    'FLIPKART': ('CH_FLIPKART', 'Flipkart'),
    'FLIPKART_OMNI': ('CH_FLIPKART', 'Flipkart'),
    'NYKAA_FASHION': ('CH_NYKAA', 'Nykaa'),
    'NYKAA': ('CH_NYKAA', 'Nykaa'),
    'AMAZON_EASYSHIP': ('CH_AMAZON', 'Amazon'),
    'AMAZON': ('CH_AMAZON', 'Amazon'),
    'AMAZON_FBA': ('CH_AMAZON', 'Amazon'),
    'AJIO_DROPSHIP-2': ('CH_AJIO', 'Ajio'),
    'AJIO_DROPSHIP': ('CH_AJIO', 'Ajio'),
    'AJIO': ('CH_AJIO', 'Ajio'),
    'SNAPDEAL': ('CH_SNAPDEAL', 'Snapdeal'),
    'TATACLIQ+2': ('CH_TATACLIQ', 'Tata Cliq'),
    'TATACLIQ': ('CH_TATACLIQ', 'Tata Cliq'),
    'SHOPIFY': ('CH_SHOPIFY', 'Shopify'),
    'BOOON': ('CH_BOOON', 'Booon'),
}

# First match wins, so put the specific words before the general ones:
# a "Denim Jacket & Skirt Co Ord Set" is a co-ord set, not a skirt.
CATEGORY_RULES = [
    ('Co-ord Set', ['co-ord', 'co ord', 'coord', 'co-ords', 'coords']),
    ('Skort',      ['skort']),
    ('Skirt',      ['skirt']),
    ('Shorts',     ['short']),
    ('Jeans',      ['jean', 'jegging']),
    ('Jacket',     ['jacket', 'shrug']),
    ('Dress',      ['dress', 'jumpsuit', 'dungaree']),
    ('T-Shirt',    ['t-shirt', 'tshirt', 't shirt', 'tee']),
    ('Trousers',   ['trouser', 'pant', 'cargo', 'palazzo', 'joggers']),
    ('Shirt',      ['shirt']),
    ('Top',        ['top', 'blouse', 'crop', 'camisole', 'bralette']),
]

COLOUR_WORDS = ['black', 'white', 'offwhite', 'off-white', 'blue', 'light blue', 'dark blue',
                'mid blue', 'navy', 'grey', 'gray', 'green', 'olive', 'pink', 'red', 'maroon',
                'yellow', 'mustard', 'beige', 'brown', 'purple', 'violet', 'orange', 'cream']

# Columns we deliberately never read.
PII_COLUMNS = ['Customer Name', 'Shipping Address Name', 'Shipping Address Line 1',
               'Shipping Address Line 2', 'Shipping Address Phone', 'AWB num',
               'Billing Address Line 1', 'Billing Address Line 2', 'Customer GSTIN',
               'Shipping GSTIN', 'Billing GSTIN', 'IMEI']


def _read_any(file) -> pd.DataFrame:
    """CSV or Excel, tolerating the cp1252 apostrophes Unicommerce writes."""
    name = getattr(file, 'name', str(file)).lower()
    if name.endswith(('.xlsx', '.xls')):
        return pd.read_excel(file, dtype=str)
    for enc in ('utf-8', 'cp1252', 'latin-1'):
        try:
            if hasattr(file, 'seek'):
                file.seek(0)
            return pd.read_csv(file, dtype=str, low_memory=False, encoding=enc)
        except UnicodeDecodeError:
            continue
    if hasattr(file, 'seek'):
        file.seek(0)
    return pd.read_csv(file, dtype=str, low_memory=False, encoding='utf-8', encoding_errors='replace')


def looks_like_tally_gst(df: pd.DataFrame) -> bool:
    cols = {str(c).strip().lower() for c in df.columns}
    return {'sale order number', 'product sku code', 'channel ledger'} <= cols


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r'[^0-9.\-]', '', regex=True),
                         errors='coerce').fillna(0.0)


def clean_product_name(raw: str) -> str:
    """'8905352028882-Belliskey Womens Dark Blue Denim Skirt' -> the words only."""
    s = re.sub(r'^\s*\d{6,}\s*-\s*', '', str(raw or ''))
    s = re.sub(r'\s*\(\d{6,}\)\s*$', '', s)            # trailing '(8905352008525)'
    s = re.sub(r'-\d{4,}-[A-Z]{4,}\s*$', '', s)        # trailing '-20262-BYWWSHR'
    s = s.replace('�', "'").replace('�', "'")
    return re.sub(r'\s+', ' ', s).strip()


def derive_category(name: str) -> str:
    n = str(name).lower()
    for label, words in CATEGORY_RULES:
        if any(w in n for w in words):
            return label
    return 'Other'


def derive_gender(name: str) -> str:
    n = str(name).lower()
    if 'women' in n or 'girl' in n or 'ladies' in n:   # check before 'men'
        return 'Women'
    if 'men' in n or 'boy' in n:
        return 'Men'
    return 'Unspecified'


def derive_colour(name: str) -> str:
    n = str(name).lower()
    for c in sorted(COLOUR_WORDS, key=len, reverse=True):
        if re.search(rf'\b{re.escape(c)}\b', n):
            return c.title()
    return ''


def parse(file, is_return: bool = False) -> tuple[pd.DataFrame, dict]:
    """Return (rows in fact_sales shape, a report describing what was read)."""
    raw = _read_any(file)
    raw.columns = [str(c).strip() for c in raw.columns]
    report: dict = {'rows_in_file': len(raw), 'source_name': getattr(file, 'name', str(file))}

    if not looks_like_tally_gst(raw):
        report['error'] = ('This does not look like a Unicommerce Tally GST report. '
                           'Expected columns Sale Order Number, Product SKU Code and Channel Ledger.')
        return pd.DataFrame(), report

    if len(raw) == 0:
        report['error'] = 'The file has column headers but no rows.'
        return pd.DataFrame(), report

    out = pd.DataFrame()
    out['sale_date'] = pd.to_datetime(raw['Date'], format='%d-%m-%Y', errors='coerce')
    bad_dates = out['sale_date'].isna()
    if bad_dates.any():   # some exports use yyyy-mm-dd
        out.loc[bad_dates, 'sale_date'] = pd.to_datetime(raw.loc[bad_dates, 'Date'],
                                                         errors='coerce', dayfirst=True)

    ledger = raw['Channel Ledger'].fillna('UNKNOWN').astype(str).str.strip()
    mapped = ledger.str.upper().map(CHANNEL_MAP)
    out['channel_id'] = [m[0] if isinstance(m, tuple) else 'CH_' + re.sub(r'[^A-Z0-9]', '', l.upper())[:12]
                         for m, l in zip(mapped, ledger)]
    out['channel_name'] = [m[1] if isinstance(m, tuple) else l.replace('_', ' ').title()
                           for m, l in zip(mapped, ledger)]
    report['unmapped_channels'] = sorted(set(ledger[mapped.isna()].str.strip()))

    out['order_id'] = raw['Sale Order Number'].astype(str).str.strip()
    _inv = raw['Invoice number'] if 'Invoice number' in raw.columns else pd.Series('', index=raw.index)
    out['invoice_no'] = _inv.fillna('').astype(str).str.strip().replace({'nan': '', 'None': ''})
    out['sku_id'] = raw['Product SKU Code'].astype(str).str.strip()
    out['product_name'] = raw['Product Name'].map(clean_product_name)
    out['category'] = out['product_name'].map(derive_category)
    out['gender'] = out['product_name'].map(derive_gender)
    out['color'] = out['product_name'].map(derive_colour)

    out['qty'] = _num(raw['Qty']).round().astype(int).abs()
    paid = _num(raw['Total']).abs()
    disc = _num(raw.get('Discount Amount', 0)).abs()
    out['net_value'] = paid
    out['discount'] = disc
    out['gross_value'] = paid + disc
    out['taxable_value'] = _num(raw.get('Sales', 0)).abs()
    tax = sum(_num(raw.get(c, 0)).abs() for c in ('CGST', 'SGST', 'IGST', 'UTGST', 'CESS'))
    out['tax_value'] = tax

    out['city'] = raw.get('Shipping Address City', '').fillna('').astype(str).str.strip().str.title()
    out['state'] = raw.get('Shipping Address State', '').fillna('').astype(str).str.strip().str.title()
    out['pincode'] = raw.get('Shipping Address Pincode', '').fillna('').astype(str).str.strip()
    out['payment_method'] = raw.get('Payment Method', '').fillna('').astype(str).str.strip().str.upper()
    out['warehouse_id'] = raw.get('Godown', '').fillna('').astype(str).str.strip()
    out['return_flag'] = bool(is_return)

    before = len(out)
    out = out[out['sku_id'].ne('') & out['sku_id'].ne('nan') & out['sale_date'].notna()]
    report['dropped_rows'] = before - len(out)
    out = out.drop_duplicates(['order_id', 'sku_id', 'invoice_no', 'return_flag'], keep='last')

    report.update(
        rows=len(out),
        date_min=out['sale_date'].min(), date_max=out['sale_date'].max(),
        units=int(out['qty'].sum()), value=float(out['net_value'].sum()),
        skus=int(out['sku_id'].nunique()),
        channels=out['channel_name'].value_counts().to_dict(),
        categories=out['category'].value_counts().to_dict(),
        is_return=bool(is_return),
    )
    return out.reset_index(drop=True), report


def sku_master_from_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """
    A provisional dim_sku built from what the sales file already tells us:
    SKU, product name, category, gender, colour, and the highest MRP seen.
    Replaced the moment the real Master sheet is loaded — that one adds
    style code, size and cost price, which no sales export contains.
    """
    if sales.empty:
        return pd.DataFrame()
    per_unit_mrp = (sales['gross_value'] / sales['qty'].replace(0, pd.NA)).round(0)
    g = sales.assign(mrp=per_unit_mrp).groupby('sku_id').agg(
        product_name=('product_name', 'last'), category=('category', 'last'),
        gender=('gender', 'last'), color=('color', 'last'), mrp=('mrp', 'max'),
    ).reset_index()
    g['style_code'] = None
    g['size'] = None
    g['cost_price'] = None
    g['launch_date'] = None
    return g
