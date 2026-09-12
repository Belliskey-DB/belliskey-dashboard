"""
SKU Master — barcodes, style numbers and FOB cost, in one place.

This is the page that makes profit possible. Everything else the dashboard
knows comes from what sold or what is on the shelf; only this says what the
thing cost to make.

Load it from the live Google Sheet, or from a file exported out of it. Rows
that cannot be trusted are shown with a reason and left out, because a wrong
cost produces a margin that looks real.
"""
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

import ui
import data
import db
import store
import skumaster as sm
from fmt import inr, inr_short, units, pct

ui.page_setup('SKU Master', '⚙️')
TO_SUPABASE = data.source() == 'supabase'

sku = data.load_skus()
cost = pd.to_numeric(sku.get('cost_price'), errors='coerce') if not sku.empty else pd.Series(dtype=float)
has_cost = int(cost.notna().sum()) if len(cost) else 0

# ---------------------------------------------------------------- where we stand
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Barcodes known', units(len(sku)))
c2.metric('With a cost', units(has_cost),
          f'{has_cost / len(sku) * 100:.0f}% of the master' if len(sku) else None, delta_color='off')
c3.metric('Styles', units(sku['style_code'].nunique() if 'style_code' in sku and not sku.empty else 0))
c4.metric('Missing a cost', units(len(sku) - has_cost), delta_color='off')
basis = data.cost_basis()
c5.metric('P&L is using', {'per_sku': 'real cost', 'blended': 'blended cost', 'none': 'no cost'}[basis['mode']],
          help=basis['label'])

# The number that actually decides whether margin is trustworthy is not how
# many barcodes carry a cost, but how much of what SOLD does.
lo, hi = data.date_bounds()
if lo and has_cost:
    sales = data.load_sales(lo, hi)
    sold = sales[~sales['return_flag']]
    if not sold.empty:
        priced = set(sku.loc[cost.notna(), 'sku_id'].astype(str))
        covered = sold['sku_id'].astype(str).isin(priced)
        u_cov = sold.loc[covered, 'qty'].sum() / sold['qty'].sum() * 100
        r_cov = sold.loc[covered, 'net_value'].sum() / sold['net_value'].sum() * 100
        st.progress(min(r_cov / 100, 1.0),
                    text=f'Cost known for {u_cov:.0f}% of units sold and {r_cov:.0f}% of revenue '
                         f'({lo:%d %b} – {hi:%d %b %Y})')
        if r_cov < 95:
            gap = (sold[~covered].groupby(['sku_id', 'product_name'])
                   .agg(units=('qty', 'sum'), revenue=('net_value', 'sum'))
                   .sort_values('revenue', ascending=False).reset_index().head(25))
            with st.expander(f'The {len(sold[~covered]):,} sold rows still without a cost — biggest first'):
                st.dataframe(gap, hide_index=True, width='stretch', column_config={
                    'sku_id': 'Barcode', 'product_name': 'Product',
                    'units': st.column_config.NumberColumn('Units', format='%d'),
                    'revenue': st.column_config.NumberColumn('Revenue ₹', format='%d')})
                ui.download(gap, 'skus_missing_cost.csv')

st.divider()

# ---------------------------------------------------------------- load it
tab_sheet, tab_file, tab_browse = st.tabs(['🔗 Google Sheet', '📄 Upload a file', '🔍 Browse the master'])


def _write(rows: pd.DataFrame, note: str) -> None:
    keep = ['sku_id', 'style_code', 'product_name', 'category', 'gender', 'color', 'size',
            'mrp', 'cost_price', 'launch_date']
    rows = rows.reindex(columns=[c for c in keep if c in rows.columns])
    if TO_SUPABASE:
        import sheets
        conn = db.get_conn()
        n = sheets.write_master(conn, rows)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sync_log (source, rows_written, status, message) "
                        "VALUES ('sku_master', %s, 'ok', %s)", (n, note[:400]))
        conn.commit()
    else:
        old = store.load('sku')
        if old is not None and not old.empty:
            merged = old.set_index('sku_id')
            incoming = rows.set_index('sku_id')
            for col in incoming.columns:
                if col not in merged.columns:
                    merged[col] = pd.NA
                # only overwrite where the sheet actually says something
                vals = incoming[col].reindex(merged.index)
                merged[col] = vals.where(vals.notna(), merged[col])
            extra = incoming[~incoming.index.isin(merged.index)]
            merged = pd.concat([merged, extra]) if len(extra) else merged
            rows = merged.reset_index()
        store.save('sku', rows, note=note)
    data.clear_cache()


def _preview_and_write(raw: pd.DataFrame, source_note: str, fx: float) -> None:
    accepted, rejected, rep = sm.parse(raw, fx_rate=fx)
    if rep.get('error'):
        st.error(rep['error'])
        return
    recognised = ', '.join(f'`{v}` → {k}' for k, v in rep['matched'].items())
    st.success(f"Read **{rep['rows_in_file']:,} rows**, priced by **{rep['level']}**. "
               f"Columns recognised: {recognised}")
    a, b, c = st.columns(3)
    a.metric('Rows accepted', units(rep['accepted']))
    b.metric('Rows rejected', units(rep['rejected']), delta_color='inverse' if rep['rejected'] else 'off')
    c.metric('Cost range', f"{inr(rep['cost_min'])} – {inr(rep['cost_max'])}" if rep['cost_min'] is not None else '–',
             help=f"average {inr(rep['cost_mean'])}" if rep['cost_mean'] else None)

    if rep['rejected']:
        with st.expander(f"{rep['rejected']} rows left out, and why", expanded=True):
            st.dataframe(rejected, hide_index=True, width='stretch')
            st.caption('Fix these in the sheet and load again. They are excluded rather than '
                       'guessed at, because a wrong cost shows up as a margin that looks real.')

    rows, info = sm.to_sku_rows(accepted, rep['level'], data.load_skus())
    if info.get('error'):
        st.error(info['error'])
        return
    if rep['level'] == 'style':
        st.info(f"Priced by style, so each cost is applied to every barcode of that style: "
                f"**{info['styles_matched']} of {info['styles_in_sheet']} styles** matched, covering "
                f"**{info['matched_skus']:,} barcodes**.", icon='🧵')
        if info['unmatched_count']:
            with st.expander(f"{info['unmatched_count']} style numbers in the sheet with no barcode on record"):
                st.write(info['styles_unmatched'])
                st.caption('Usually the same style typed differently in the two files. '
                           'Anything left here gets no cost.')
    elif info.get('unknown_count'):
        st.warning(f"{info['unknown_count']} barcodes in the sheet are not in the master. They will be "
                   f"added. First few: {', '.join(map(str, info['unknown_skus'][:6]))}")

    if rows.empty:
        st.warning('Nothing to load once the rows were matched up.')
        return
    st.dataframe(rows.head(20), hide_index=True, width='stretch')
    if st.button(f'Load cost for {len(rows):,} barcodes', type='primary', key=f'w_{source_note}'):
        _write(rows, source_note)
        st.success(f'Done. {len(rows):,} barcodes now carry a cost — the P&L has switched to real margin.')
        st.balloons()
        st.rerun()


with tab_sheet:
    st.markdown('#### Sync straight from the Google Sheet')
    # In TOML everything after a [table] header belongs to that table until the
    # next header, whatever the indentation. So SKU_MASTER_SHEET_ID pasted below
    # [gcp_service_account] silently becomes a key INSIDE it and the app sees
    # nothing at the top level. Look in both places rather than making anyone
    # debug a file format.
    SA_KEYS = {'type', 'project_id', 'private_key_id', 'private_key', 'client_email',
               'client_id', 'auth_uri', 'token_uri', 'auth_provider_x509_cert_url',
               'client_x509_cert_url', 'universe_domain'}
    sa_raw, misplaced = {}, []
    try:
        secrets_ok = 'gcp_service_account' in st.secrets
        sa_raw = dict(st.secrets['gcp_service_account']) if secrets_ok else {}
        misplaced = [k for k in sa_raw if k not in SA_KEYS]

        def _secret(name: str) -> str:
            return str(st.secrets.get(name, '') or sa_raw.get(name, '') or '')

        default_url = _secret('SKU_MASTER_SHEET_ID')
        default_gid = _secret('SKU_MASTER_GID')
        sa_email = str(sa_raw.get('client_email', ''))
    except Exception:
        secrets_ok, default_url, default_gid, sa_email = False, '', '', ''
    # Credentials only wants the service-account fields.
    sa_info = {k: v for k, v in sa_raw.items() if k in SA_KEYS}

    if not secrets_ok:
        st.warning('No Google service account is set on this app yet, so it cannot read the sheet.',
                   icon='🔑')
        st.markdown(
            '**You already have one.** The Tales & Stories app uses a service account for its own '
            'sheet sync, and the same one works here — nothing new to create in Google Cloud.\n\n'
            '1. In Streamlit, open the **Tales & Stories** app → ⋮ → Settings → **Secrets**. Copy the '
            'whole `[gcp_service_account]` block, from that line down to the last key.\n'
            '2. Open the Belliskey master sheet → **Share** → paste the `client_email` from that '
            'block → give it **Viewer** → Send.\n'
            '3. Come back to **this** app → ⋮ → Settings → **Secrets**, paste the block, and add:\n'
            '```toml\nSKU_MASTER_SHEET_ID = "the sheet id or full link"\nSKU_MASTER_GID = "2011622154"\n```\n'
            '4. Save. The app restarts, and this tab turns into a one-click sync.')
    else:
        st.success(f'Service account connected: `{sa_email}`')
        st.caption('The sheet must be shared with that address as Viewer, or the read is refused.')
        if misplaced:
            st.info('Note: ' + ', '.join(f'`{k}`' for k in misplaced) + ' were pasted **below** the '
                    '`[gcp_service_account]` line in Secrets. In TOML that puts them inside it, so '
                    'they are not top-level settings. They are being read anyway, but move them '
                    '**above** that line to keep the file honest.', icon='📝')

    url = st.text_input('Sheet link or id', value=default_url,
                        placeholder='https://docs.google.com/spreadsheets/d/…')
    sheet_id, gid_from_url = sm.parse_sheet_ref(url) if url else ('', None)
    gid = st.text_input('Tab gid', value=(gid_from_url or default_gid),
                        help='The number after gid= in the sheet link. Leave blank for the first '
                             'tab. A gid keeps pointing at the right tab even if it is renamed.')
    fx = st.number_input('Multiply the cost by', value=1.0, step=0.01, min_value=0.0,
                         help='Leave at 1 — Belliskey quotes FOB in rupees.')

    if st.button('🔄 Sync now', type='primary', disabled=not (secrets_ok and url)):
        try:
            with st.spinner('Reading the sheet…'):
                raw, tab_title = sm.read_google_sheet(sheet_id, None, sa_info, gid=gid or None)
            st.session_state['sku_sheet_raw'] = raw
            st.session_state['sku_sheet_tab'] = tab_title
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            st.error('Could not read that sheet.')
            low = msg.lower()
            if 'permission' in low or '403' in msg:
                st.markdown(f'Share the sheet with **{sa_email or "the service account"}** as '
                            f'**Viewer**. That is the usual cause.')
            elif 'not found' in low or '404' in msg:
                st.markdown('Check the link. If the gid is wrong the tab will not be found either — '
                            'clear it to read the first tab.')
            elif 'api has not been used' in low or 'disabled' in low:
                st.markdown('Enable **Google Sheets API** and **Google Drive API** on that Cloud project.')
            st.code(msg[:400], language='text')

    if isinstance(st.session_state.get('sku_sheet_raw'), pd.DataFrame):
        tt = st.session_state.get('sku_sheet_tab')
        if tt:
            st.caption(f'Read from tab **{tt}**')
        _preview_and_write(st.session_state['sku_sheet_raw'], f'google sheet · {tt or ""}'.strip(), fx)


with tab_file:
    st.markdown('#### Or export the sheet and drop it here')
    st.caption('In Google Sheets: File → Download → Microsoft Excel or CSV. Needs no setup at all.')
    fx2 = st.number_input('Multiply the cost by', value=1.0, step=0.01, min_value=0.0, key='fx2',
                          help='Leave at 1 when FOB is already in rupees.')
    f = st.file_uploader('Master sheet export', type=['xlsx', 'xls', 'csv'], key='up_master_page')
    if f:
        if f.name.lower().endswith('.csv'):
            raw = pd.read_csv(f, dtype=str)
        else:
            xl = pd.ExcelFile(f)
            sheet = xl.sheet_names[0]
            if len(xl.sheet_names) > 1:
                sheet = st.selectbox('Which tab?', xl.sheet_names, key='master_tab_pick')
            raw = xl.parse(sheet, dtype=str)
        _preview_and_write(raw, f.name, fx2)


with tab_browse:
    if sku.empty:
        st.info('Nothing loaded yet.')
    else:
        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            q = st.text_input('Search barcode, style or product', '')
        with f2:
            cats = ['All'] + sorted(sku['category'].dropna().unique().tolist()) if 'category' in sku else ['All']
            pick_cat = st.selectbox('Category', cats)
        with f3:
            only = st.selectbox('Show', ['Everything', 'With a cost', 'Missing a cost'])
        v = sku.copy()
        v['cost_price'] = pd.to_numeric(v.get('cost_price'), errors='coerce')
        v['mrp'] = pd.to_numeric(v.get('mrp'), errors='coerce')
        if q:
            mask = False
            for c in ('sku_id', 'style_code', 'product_name'):
                if c in v:
                    # regex=False: the box is a search term, not a pattern — a stray
                    # bracket would otherwise raise straight out of the page.
                    mask = mask | v[c].astype(str).str.contains(q, case=False, na=False, regex=False)
            v = v[mask]
        if pick_cat != 'All':
            v = v[v['category'] == pick_cat]
        if only == 'With a cost':
            v = v[v['cost_price'].notna()]
        elif only == 'Missing a cost':
            v = v[v['cost_price'].isna()]
        v['margin_at_mrp'] = (1 - v['cost_price'] / v['mrp'].replace(0, np.nan)) * 100
        st.caption(f'{len(v):,} of {len(sku):,} barcodes')
        st.dataframe(
            v[[c for c in ['sku_id', 'style_code', 'product_name', 'category', 'gender', 'color',
                           'size', 'mrp', 'cost_price', 'margin_at_mrp'] if c in v]],
            hide_index=True, width='stretch', height=460, column_config={
                'sku_id': 'Barcode', 'style_code': 'Style', 'product_name': 'Product',
                'category': 'Category', 'gender': 'For', 'color': 'Colour', 'size': 'Size',
                'mrp': st.column_config.NumberColumn('MRP ₹', format='%d'),
                'cost_price': st.column_config.NumberColumn('Cost ₹', format='%d'),
                'margin_at_mrp': st.column_config.NumberColumn('Margin at MRP', format='%.0f%%')})
        ui.download(v, f'sku_master_{date.today()}.csv')
