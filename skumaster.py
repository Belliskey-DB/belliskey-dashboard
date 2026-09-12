"""
skumaster.py — the SKU master: barcodes, style numbers and FOB cost.

This is the file that turns the dashboard from "revenue after deductions" into
real profit, because it is the only place a cost per item exists. The supplier
ledger can only ever give a blended average across everything bought; this
gives the cost of the actual thing that sold.

Two shapes are accepted, because a costing sheet is usually kept one way or
the other and neither is wrong:

  SKU level     one row per barcode. Used as-is.
  STYLE level   one row per style, no barcode. The cost is applied to every
                barcode of that style, which is how FOB is quoted anyway —
                a size 28 and a size 32 of the same skirt cost the same to make.

Reads either a live Google Sheet (service account) or a file exported from it.
Rows that cannot be trusted are rejected with a reason rather than silently
loaded, because a wrong cost is worse than a missing one: it produces a margin
that looks real.
"""
from __future__ import annotations

import re
import pandas as pd

# target -> header names that mean it, matched case- and space-insensitively
COLUMNS = {
    'sku_id':      ['sku', 'sku code', 'sku id', 'barcode', 'ean', 'ean code', 'item sku',
                    'seller sku', 'product code', 'uniware sku'],
    # 'Article no' comes FIRST on purpose. Belliskey's costing sheet carries both
    # "Style No." (20137-10) and "Article No." (20137-10-BYWWSHR), and it is the
    # article that matches the style code already in the master — 578 of 583
    # against 0 of 550. Taking the wrong one silently re-keys every style.
    'style_code':  ['article no', 'article', 'article code', 'style code', 'style no',
                    'style number', 'style', 'design no', 'design', 'style name'],
    # 'Product' holds the category (Jeans, Shorts); 'Product Type' holds the
    # descriptive name (HIGH WAIST DENIM SHORTS). Reading those the other way
    # round replaces a clean 14-category list with hundreds of descriptions.
    'product_name': ['product type', 'product name', 'item description', 'description',
                     'item name', 'item', 'title'],
    'category':    ['category', 'product', 'garment type', 'type'],
    'gender':      ['gender', 'for', 'segment'],
    'color':       ['colour', 'color', 'shade'],
    'size':        ['size'],
    'mrp':         ['mrp', 'max retail price', 'retail price', 'list price'],
    'cost_price':  ['fob', 'fob price', 'fob rate', 'fob cost', 'fob value', 'cost', 'cost price',
                    'landed cost', 'unit cost', 'purchase price', 'factory price', 'making cost',
                    'cp', 'rate'],
    'currency':    ['currency', 'curr', 'ccy'],
    'launch_date': ['launch date', 'launch', 'live date', 'listing date', 'season start'],
}
IDENTIFIERS = ('sku_id', 'style_code')


def _key(s) -> str:
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def map_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Rename the sheet's headers to our names. Returns (frame, which header matched what)."""
    lookup = {}
    for col in df.columns:
        lookup.setdefault(_key(col), col)
    rename, matched = {}, {}
    for target, names in COLUMNS.items():
        for n in names:
            actual = lookup.get(_key(n))
            if actual is not None and actual not in rename:
                rename[actual] = target
                matched[target] = actual
                break
    out = df.rename(columns=rename)
    keep = [c for c in COLUMNS if c in out.columns]
    return out[keep].copy(), matched


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.replace(r'[₹$,\s]', '', regex=True).replace({'': None, 'nan': None}),
        errors='coerce')


def _text(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().replace({'nan': None, 'None': None, '': None})


def _gender(s: pd.Series) -> pd.Series:
    """\"Women's\" and \"Womens\" are the same thing the master already calls \"Women\"."""
    return (s.fillna('').astype(str).str.strip()
             .str.replace(r"[\u2019']s$", '', regex=True)
             .str.replace(r"s'$", '', regex=True)
             .str.title().replace({'': None, 'Nan': None}))


def parse(df: pd.DataFrame, fx_rate: float = 1.0) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Returns (accepted, rejected, report).

    fx_rate multiplies the cost, for a sheet quoting FOB in a foreign currency.
    """
    raw = df.copy()
    raw.columns = [str(c).strip() for c in raw.columns]
    mapped, matched = map_columns(raw)
    report: dict = {'matched': matched, 'headers_seen': list(raw.columns), 'rows_in_file': len(raw)}

    present = [c for c in IDENTIFIERS if c in mapped.columns]
    if not present:
        report['error'] = ('No barcode and no style number found. One of them is needed to know '
                           'what a cost belongs to. Headers seen: ' + ', '.join(map(str, raw.columns)))
        return pd.DataFrame(), pd.DataFrame(), report
    if 'cost_price' not in mapped.columns:
        report['error'] = ('No FOB or cost column found. Headers seen: '
                           + ', '.join(map(str, raw.columns)))
        return pd.DataFrame(), pd.DataFrame(), report

    for c in ('sku_id', 'style_code', 'product_name', 'category', 'gender', 'color', 'size', 'currency'):
        if c in mapped.columns:
            mapped[c] = _text(mapped[c])
    if 'gender' in mapped.columns:
        mapped['gender'] = _gender(mapped['gender'])
    if 'category' in mapped.columns:
        # The costing sheet drifts the same way the stock workbook does:
        # 'jeans' and 'Jeans', 'Skirt' and 'Skirts', 'T-Shirt' and 'T-shirt'.
        # Reuse one canonical list so both files land on the same categories.
        from stockfile import CANONICAL, _key as _ckey
        mapped['category'] = mapped['category'].map(
            lambda v: CANONICAL.get(_ckey(v), str(v).title()) if pd.notna(v) and str(v).strip() else None)
    for c in ('mrp', 'cost_price'):
        if c in mapped.columns:
            mapped[c] = _num(mapped[c])
    if 'launch_date' in mapped.columns:
        mapped['launch_date'] = pd.to_datetime(mapped['launch_date'], errors='coerce', dayfirst=True).dt.date

    # A sheet is SKU level only if it actually carries barcodes.
    level = 'sku' if ('sku_id' in mapped.columns and mapped['sku_id'].notna().any()) else 'style'
    report['level'] = level

    if fx_rate and fx_rate != 1.0:
        mapped['cost_price'] = mapped['cost_price'] * float(fx_rate)

    key = 'sku_id' if level == 'sku' else 'style_code'
    mapped = mapped[mapped[key].notna()]

    # ---- validation. A wrong cost is worse than a missing one.
    reasons = pd.Series('', index=mapped.index)
    cost = mapped['cost_price']
    reasons[cost.isna()] = 'no cost value'
    reasons[(reasons == '') & (cost <= 0)] = 'cost is zero or negative'
    if 'mrp' in mapped.columns:
        bad = (reasons == '') & mapped['mrp'].notna() & (mapped['mrp'] > 0) & (cost > mapped['mrp'])
        reasons[bad] = 'cost is higher than MRP'
    dupe = mapped.duplicated(key, keep='last') & (reasons == '')
    reasons[dupe] = f'duplicate {key}, an earlier row was superseded'

    rejected = mapped[reasons != ''].copy()
    rejected['reason'] = reasons[reasons != '']
    accepted = mapped[reasons == ''].copy()

    report.update(
        accepted=len(accepted), rejected=len(rejected),
        key=key,
        cost_min=float(accepted['cost_price'].min()) if len(accepted) else None,
        cost_max=float(accepted['cost_price'].max()) if len(accepted) else None,
        cost_mean=float(accepted['cost_price'].mean()) if len(accepted) else None,
        reject_reasons=rejected['reason'].value_counts().to_dict() if len(rejected) else {},
    )
    return accepted.reset_index(drop=True), rejected.reset_index(drop=True), report


def to_sku_rows(accepted: pd.DataFrame, level: str, known_skus: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Turn accepted rows into one row per barcode, ready for dim_sku.

    A style-level sheet is fanned out across every barcode of that style using
    the SKU list already loaded from her stock workbook. Styles with no barcode
    on record are reported rather than dropped quietly — they are usually a
    style number typed differently in the two files.
    """
    info: dict = {}
    if accepted.empty:
        return pd.DataFrame(), info

    if level == 'sku':
        out = accepted.copy()
        info['matched_skus'] = len(out)
        if known_skus is not None and not known_skus.empty:
            known = set(known_skus['sku_id'].astype(str))
            unknown = out.loc[~out['sku_id'].astype(str).isin(known), 'sku_id']
            info['unknown_skus'] = unknown.tolist()[:50]
            info['unknown_count'] = int(len(unknown))
        return out, info

    # style level -> fan out
    if known_skus is None or known_skus.empty or 'style_code' not in known_skus.columns:
        info['error'] = ('This sheet is priced by style, so the barcodes for each style are needed '
                         'to apply it. Load her stock workbook on the Data Hub first.')
        return pd.DataFrame(), info

    ks = known_skus[['sku_id', 'style_code']].dropna(subset=['style_code']).copy()
    ks['_k'] = ks['style_code'].astype(str).str.strip().str.upper()
    src = accepted.copy()
    src['_k'] = src['style_code'].astype(str).str.strip().str.upper()

    merged = ks.merge(src.drop(columns=['sku_id'], errors='ignore'), on='_k', how='inner',
                      suffixes=('', '_sheet'))
    merged['style_code'] = merged['style_code'].fillna(merged.get('style_code_sheet'))
    out = merged.drop(columns=[c for c in merged.columns if c.endswith('_sheet') or c == '_k'],
                      errors='ignore')

    priced = set(src['_k'])
    known_styles = set(ks['_k'])
    info['styles_in_sheet'] = len(priced)
    info['styles_matched'] = len(priced & known_styles)
    info['styles_unmatched'] = sorted(priced - known_styles)[:50]
    info['unmatched_count'] = len(priced - known_styles)
    info['matched_skus'] = len(out)
    return out.reset_index(drop=True), info


def read_google_sheet(sheet_id: str, tab: str | None = None,
                      service_account_info: dict | None = None,
                      gid: int | str | None = None) -> tuple[pd.DataFrame, str]:
    """
    Read one tab of a Google Sheet. Returns (frame, the tab's name).

    A tab can be named or identified by gid. The gid is what a Sheets URL
    carries (…#gid=2011622154) and it is stable when someone renames the tab,
    so it is preferred when both are given.

    The service account needs Viewer access on the sheet.
    """
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(service_account_info, scopes=[
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'])
    sh = gspread.authorize(creds).open_by_key(sheet_id)
    if gid not in (None, '', 'None'):
        ws = sh.get_worksheet_by_id(int(gid))
    elif tab:
        ws = sh.worksheet(tab)
    else:
        ws = sh.get_worksheet(0)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(), ws.title
    # The header is the first row with at least three filled cells — costing
    # sheets usually carry a title and a blank line above the real table.
    hdr = next((i for i, r in enumerate(values) if sum(bool(str(c).strip()) for c in r) >= 3), 0)
    body = values[hdr + 1:]
    cols = values[hdr]
    seen: dict[str, int] = {}
    uniq = []
    for c in cols:
        c = str(c).strip() or 'unnamed'
        seen[c] = seen.get(c, 0) + 1
        uniq.append(c if seen[c] == 1 else f'{c}_{seen[c]}')
    return pd.DataFrame(body, columns=uniq).replace('', None), ws.title


def parse_sheet_ref(url: str) -> tuple[str, str | None]:
    """
    Pull the sheet id and the tab gid out of whatever was pasted.

    Accepts a full URL (…/d/<id>/edit?…#gid=<gid>), or a bare id. Returning the
    gid matters: a link copied from the browser points at the tab the person was
    looking at, and reading the first tab instead would quietly load the wrong data.
    """
    text = str(url).strip()
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', text)
    sheet_id = m.group(1) if m else text.split('?')[0].split('#')[0].strip()
    g = re.search(r'[#&?]gid=(\d+)', text)
    return sheet_id, (g.group(1) if g else None)


def sheet_id_from_url(url: str) -> str:
    """Backwards-compatible shim."""
    return parse_sheet_ref(url)[0]
