"""
purchases.py — read the Tally ledger extract of what Belliskey buys.

The file is her supplier's ledger for the account "BELLISKEY LLP (S)", exported
from Tally. Around fifteen preamble rows of letterhead sit above a real table,
so the header row has to be found rather than assumed.

    Date | Particulars | Voucher Type | Voucher No. | Voucher Ref. No. |
    PAN No. | Quantity | Value | Gross Total | <ledger columns that vary by file>

Only the first nine columns are stable. The ledger columns after them differ
between financial years (different bank and card accounts), so they are ignored.

IT IS INVOICE LEVEL, NOT SKU LEVEL. There is no barcode here, so this can never
give a per-style cost. What it does give is a truthful blended cost per unit:
total value bought divided by total units bought. That is what turns the P&L
from "revenue after deductions" into an approximate margin.

Voucher types, read from the supplier's side:
    Sales        the supplier sold to Belliskey  -> a purchase, positive
    Credit Note  goods went back to the supplier -> negative quantity and value
"""
from __future__ import annotations

import re
import pandas as pd

REQUIRED = ['Date', 'Voucher Type', 'Quantity', 'Value']
STABLE = ['Date', 'Particulars', 'Voucher Type', 'Voucher No.', 'Voucher Ref. No.',
          'PAN No.', 'Quantity', 'Value', 'Gross Total']


def _read_raw(file) -> pd.DataFrame:
    name = getattr(file, 'name', str(file)).lower()
    if name.endswith('.csv'):
        return pd.read_csv(file, header=None, dtype=str)
    return pd.read_excel(file, sheet_name=0, header=None, dtype=str)


def _find_header(raw: pd.DataFrame) -> int | None:
    """The header row is the first one carrying both 'Voucher Type' and 'Quantity'."""
    for i in range(min(len(raw), 60)):
        cells = {re.sub(r'\s+', ' ', str(v)).strip().lower() for v in raw.iloc[i] if pd.notna(v)}
        if 'voucher type' in cells and 'quantity' in cells:
            return i
    return None


def _supplier(raw: pd.DataFrame) -> str:
    for i in range(min(len(raw), 5)):
        v = raw.iloc[i, 0]
        if pd.notna(v) and str(v).strip():
            return re.sub(r'\s*\d{2}-\d{2}.*$', '', str(v).strip()).strip()
    return ''


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(r'[^0-9.\-]', '', regex=True), errors='coerce')


def parse(file) -> tuple[pd.DataFrame, dict]:
    raw = _read_raw(file)
    hdr = _find_header(raw)
    if hdr is None:
        return pd.DataFrame(), {'error': 'Could not find the table header. Expected a row containing '
                                         '"Voucher Type" and "Quantity" — is this the Tally ledger export?'}
    supplier = _supplier(raw)
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [re.sub(r'\s+', ' ', str(v)).strip() for v in raw.iloc[hdr]]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return pd.DataFrame(), {'error': f'Missing column(s): {", ".join(missing)}. '
                                         f'Found: {", ".join(str(c) for c in df.columns if str(c) != "nan")}'}

    # The last row is Tally's "Grand Total" — a summary, not a transaction.
    df = df[df['Voucher Type'].notna()]
    df = df[~df['Date'].astype(str).str.strip().str.lower().isin(['grand total', 'total', 'nan', ''])]

    out = pd.DataFrame({
        'purchase_date': pd.to_datetime(df['Date'], errors='coerce'),
        'supplier': supplier,
        'voucher_type': df['Voucher Type'].astype(str).str.strip(),
        'voucher_no': df.get('Voucher No.', pd.Series('', index=df.index)).astype(str).str.strip(),
        'qty': _num(df['Quantity']),
        'value': _num(df['Value']),
        'gross_total': _num(df.get('Gross Total', pd.Series(0, index=df.index))),
    })
    out = out[out['purchase_date'].notna() & out['qty'].notna()]

    # A credit note is a return to the supplier: Tally writes the quantity and
    # value negative but still prints Gross Total positive. Make the sign
    # consistent so totals add up instead of cancelling wrongly.
    ret = out['qty'] < 0
    out.loc[ret, 'gross_total'] = -out.loc[ret, 'gross_total'].abs()

    out['qty'] = out['qty'].round().astype(int)
    out['fy'] = out['purchase_date'].map(lambda d: f'{d.year}-{str(d.year + 1)[2:]}' if d.month >= 4
                                         else f'{d.year - 1}-{str(d.year)[2:]}')
    out = out.drop_duplicates(['supplier', 'voucher_no', 'purchase_date'], keep='last')

    units, value = int(out['qty'].sum()), float(out['value'].sum())
    rep = {
        'rows': len(out), 'supplier': supplier,
        'date_min': out['purchase_date'].min(), 'date_max': out['purchase_date'].max(),
        'units': units, 'value': value,
        'gross_total': float(out['gross_total'].sum()),
        'cost_per_unit': (value / units) if units else None,
        'credit_notes': int((out['qty'] < 0).sum()),
        'by_fy': {fy: {'units': int(g['qty'].sum()), 'value': float(g['value'].sum()),
                       'cost_per_unit': float(g['value'].sum() / g['qty'].sum()) if g['qty'].sum() else None}
                  for fy, g in out.groupby('fy')},
    }
    return out.reset_index(drop=True), rep


def blended_cost_per_unit(purchases: pd.DataFrame, fy: str | None = None) -> float | None:
    """Total value bought divided by total units bought. None when nothing is loaded."""
    if purchases is None or purchases.empty:
        return None
    df = purchases[purchases['fy'] == fy] if fy else purchases
    units = pd.to_numeric(df['qty'], errors='coerce').sum()
    value = pd.to_numeric(df['value'], errors='coerce').sum()
    return float(value / units) if units else None
