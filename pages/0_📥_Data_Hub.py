"""
Data Hub — every way data gets into the dashboard, in one place.

Tab 1  Unicommerce sales and returns (the Tally GST export)
Tab 2  Master sheet  -> SKU costs, style codes, sizes
Tab 3  Production sheet -> open lots
Tab 4  Stock export  -> what is on hand
Tab 5  What is loaded right now
"""
from datetime import date
import pandas as pd
import streamlit as st

import ui
import data
import db
import store
import sheets
import stockfile as sfile
import purchases as pur
import unicommerce as uc
from fmt import inr, inr_short, units

ui.page_setup('Data Hub', '📥')
st.caption('Upload a file, see exactly what was read, then write it. Nothing is saved until you press the button.')

TO_SUPABASE = data.source() == 'supabase'
if not TO_SUPABASE:
    if store.is_ephemeral():
        st.warning('Supabase is not connected, so an upload is held in **temporary storage on this '
                   'server**. It stays while the app is awake, and is cleared whenever the app '
                   'restarts or goes to sleep after a spell of no use — then you simply upload '
                   'again. Connecting Supabase is what makes it permanent.', icon='⏳')
    else:
        st.info('Supabase is not connected yet, so uploads are saved to a **file in this folder** '
                'and the pages read from there. That is enough to look at the numbers today.', icon='💾')

t1, tS, tP, t2, t3, t4, t5 = st.tabs(
    ['🛒 Unicommerce sales', '🗄 Stock file', '🧾 Purchases', '📗 Master sheet',
     '🛠 Production sheet', '📦 Uniware stock', '📋 What is loaded'])


def _report_card(rep: dict) -> bool:
    if rep.get('error'):
        st.error(rep['error'])
        return False
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Rows read', units(rep['rows']))
    c2.metric('Units', units(rep['units']))
    c3.metric('Value', inr_short(rep['value']))
    c4.metric('SKUs', units(rep['skus']))
    st.caption(f"{rep['date_min']:%d %b %Y} → {rep['date_max']:%d %b %Y}"
               + (f" · {rep['dropped_rows']} rows skipped (no SKU or no date)" if rep.get('dropped_rows') else ''))
    a, b = st.columns(2)
    with a:
        st.markdown('**Channels found**')
        st.dataframe(pd.DataFrame(rep['channels'].items(), columns=['Channel', 'Rows']),
                     hide_index=True, width='stretch')
    with b:
        st.markdown('**Categories read from the product name**')
        st.dataframe(pd.DataFrame(rep['categories'].items(), columns=['Category', 'Rows']),
                     hide_index=True, width='stretch')
    if rep.get('unmapped_channels'):
        st.warning('Channel names not recognised, kept under their own name: '
                   + ', '.join(rep['unmapped_channels'])
                   + '. Add them to CHANNEL_MAP in unicommerce.py to group them properly.')
    return True


# ---------------------------------------------------------------- 1. Unicommerce
with t1:
    st.markdown('#### Unicommerce → Reports → Tally GST Report')
    st.markdown('Upload the **sales** export and, separately, the **return** export. '
                'Loading the same period twice is safe: rows are matched on invoice and SKU, '
                'so nothing is double counted.')
    col_a, col_b = st.columns(2)
    with col_a:
        sales_file = st.file_uploader('Sales export (Tally GST Report)', type=['csv', 'xlsx', 'xls'], key='uc_sales')
    with col_b:
        ret_file = st.file_uploader('Returns export (Tally Return GST Report)', type=['csv', 'xlsx', 'xls'], key='uc_ret')

    for f, is_ret, label in ((sales_file, False, 'sales'), (ret_file, True, 'returns')):
        if not f:
            continue
        st.divider()
        st.markdown(f'##### {f.name}')
        with st.spinner('Reading…'):
            df, rep = uc.parse(f, is_return=is_ret)
        if not _report_card(rep):
            if 'no rows' in str(rep.get('error', '')) and is_ret:
                st.info('An empty return file usually means the date range or the channel filter on the '
                        'Unicommerce report excluded everything. Re-run the report for the same dates as '
                        'the sales export, with no channel filter.', icon='ℹ️')
            continue
        with st.expander('First 20 rows, exactly as they will be stored'):
            st.dataframe(df.head(20), hide_index=True, width='stretch')
        st.caption('Customer names, addresses, phone numbers and AWB numbers are not read. '
                   'City and state are kept for the geography view.')

        if st.button(f'Load {len(df):,} {label} rows', key=f'btn_uc_{label}', type='primary'):
            if TO_SUPABASE:
                from psycopg2.extras import execute_values
                conn = db.get_conn()
                cols = ['sale_date', 'channel_id', 'warehouse_id', 'order_id', 'invoice_no', 'sku_id',
                        'qty', 'gross_value', 'discount', 'net_value', 'taxable_value', 'tax_value',
                        'city', 'state', 'pincode', 'payment_method', 'return_flag']
                rows = [tuple(r) for r in df.reindex(columns=cols).astype(object)
                        .where(pd.notna(df.reindex(columns=cols)), None).itertuples(index=False, name=None)]
                with conn.cursor() as cur:
                    # Register any marketplace this file contains before inserting sales,
                    # otherwise the channel_id foreign key rejects the whole batch the
                    # first time she sells on a marketplace we have not seen before.
                    chans = df[['channel_id', 'channel_name']].drop_duplicates()
                    execute_values(cur, """INSERT INTO dim_channel (channel_id, channel_name)
                        VALUES %s ON CONFLICT (channel_id) DO NOTHING""",
                        [tuple(r) for r in chans.itertuples(index=False, name=None)])
                    execute_values(cur, f"""INSERT INTO fact_sales ({', '.join(cols)}) VALUES %s
                        ON CONFLICT (order_id, sku_id, invoice_no, return_flag) DO UPDATE SET
                        qty=EXCLUDED.qty, gross_value=EXCLUDED.gross_value, discount=EXCLUDED.discount,
                        net_value=EXCLUDED.net_value, taxable_value=EXCLUDED.taxable_value,
                        tax_value=EXCLUDED.tax_value""", rows, page_size=1000)
                    cur.execute("INSERT INTO sync_log (source, rows_written, status, message) "
                                "VALUES (%s,%s,'ok',%s)", (f'unicommerce_{label}', len(rows), f.name))
                conn.commit()
                sheets.write_master(conn, uc.sku_master_from_sales(df))
                st.success(f'{len(rows):,} rows written to Supabase.')
            else:
                added, total = store.append('sales', df, ['order_id', 'sku_id', 'invoice_no', 'return_flag'],
                                            note=f.name)
                # keep a provisional SKU list so pages have product names and categories
                allsales = store.load('sales')
                prov = uc.sku_master_from_sales(allsales)
                existing = store.load('sku')
                if existing is not None and 'cost_price' in existing:
                    # never overwrite real costs from the Master sheet
                    keep = existing[pd.to_numeric(existing['cost_price'], errors='coerce').notna()]
                    prov = pd.concat([prov[~prov['sku_id'].isin(keep['sku_id'])], keep], ignore_index=True)
                store.save('sku', prov, note='derived from sales file')
                st.success(f'{added:,} new rows added — {total:,} rows stored in total.')
            data.clear_cache()
            st.balloons()


# ---------------------------------------------------------------- stock file
with tS:
    st.markdown('#### Her own stock workbook (STOCK FILE NEW.xlsm)')
    st.markdown('This one file does two jobs: it is the **SKU master** — the only place style code, '
                'size and MRP exist, since the Unicommerce export has none of them — and it is the '
                '**stock on hand**.')
    as_of = st.date_input('Stock is correct as at', value=date.today(), key='sf_date',
                          help='The date the counts in the file were taken, not today\'s date.')
    f = st.file_uploader('STOCK FILE …xlsm', type=['xlsm', 'xlsx', 'xls'], key='up_stockfile')
    if f:
        with st.spinner('Reading…'):
            sku_df, stock_df, rep = sfile.parse_inventory(f, as_of)
        if rep.get('error'):
            st.error(rep['error'])
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric('Barcodes', units(rep['skus']))
            c2.metric('Styles', units(rep['styles']))
            c3.metric('Units on hand', units(rep['units']))
            c4.metric('Value at MRP', inr_short(rep['stock_value_at_mrp']))
            st.caption(f"{rep['with_mrp']:,} of {rep['skus']:,} barcodes carry an MRP · "
                       f"{rep['out_of_stock']:,} are out of stock")
            cats = pd.DataFrame(rep['categories'].items(), columns=['Category', 'Barcodes'])
            a, b = st.columns([2, 3])
            with a:
                st.dataframe(cats, hide_index=True, width='stretch')
            with b:
                st.dataframe(sku_df.head(12), hide_index=True, width='stretch')
            st.caption('Spellings are normalised on the way in — her sheet has "Women\'s skirt", '
                       '"Women\'s Skirt" and "Women\'s Skirts", which are one category, not three.')

            if st.button(f'Load {len(sku_df):,} barcodes and the stock count', key='btn_sf', type='primary'):
                if TO_SUPABASE:
                    conn = db.get_conn()
                    sheets.write_master(conn, sku_df)
                    from psycopg2.extras import execute_values
                    with conn.cursor() as cur:
                        cur.execute('DELETE FROM fact_stock_snapshot WHERE snapshot_date = %s', (as_of,))
                        execute_values(cur, 'INSERT INTO fact_stock_snapshot (snapshot_date, sku_id, '
                                            'warehouse_id, stock_qty) VALUES %s',
                                       [(as_of, r.sku_id, r.warehouse_id, int(r.stock_qty))
                                        for r in stock_df.itertuples()], page_size=1000)
                        cur.execute("INSERT INTO sync_log (source, rows_written, status, message) "
                                    "VALUES ('stock_file', %s, 'ok', %s)", (len(sku_df), f.name))
                    conn.commit()
                else:
                    old = store.load('sku')
                    merged = sku_df
                    if old is not None and not old.empty:
                        keep_cost = old[pd.to_numeric(old.get('cost_price'), errors='coerce').notna()]
                        merged = pd.concat([sku_df[~sku_df['sku_id'].isin(keep_cost['sku_id'])], keep_cost],
                                           ignore_index=True) if len(keep_cost) else sku_df
                    store.save('sku', merged, note=f.name)
                    store.save('stock', stock_df, note=f'{f.name} @ {as_of:%d %b %Y}')
                data.clear_cache()
                st.success('Loaded. The Inventory page and the style names on Sales are live now.')
                st.balloons()

        with st.expander('Also in this workbook: movement history'):
            h, hrep = sfile.parse_history(f)
            if hrep.get('error'):
                st.caption(hrep['error'])
            else:
                st.markdown(f"**{hrep['rows']:,} movements** read, {hrep['date_min']:%d %b} to "
                            f"{hrep['date_max']:%d %b %Y} — {hrep['dispatched']:,} units dispatched, "
                            f"{hrep['returned']:,} returned.")
                st.dataframe(h.head(10), hide_index=True, width='stretch')
                st.warning('Not loaded, on purpose. Roughly a third of the rows have dates Excel has '
                           'mangled into month/day order, and the returns here carry no value and no '
                           'marketplace — so they cannot stand in for the Unicommerce return export.',
                           icon='⚠️')


# ---------------------------------------------------------------- purchases
with tP:
    st.markdown('#### Supplier ledger (Tally export)')
    st.markdown('Upload one file per financial year. This is what turns the P&L from '
                '"revenue after deductions" into an approximate margin.')
    st.caption('It is invoice level, not barcode level, so it gives a blended cost per unit '
               'across everything bought — never a per-style cost.')
    fs = st.file_uploader('Ledger export (.xlsx or .csv)', type=['xlsx', 'xls', 'csv'],
                          key='up_purchase', accept_multiple_files=True)
    for f in fs or []:
        st.divider()
        st.markdown(f'##### {f.name}')
        df, rep = pur.parse(f)
        if rep.get('error'):
            st.error(rep['error'])
            continue
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Invoices', units(rep['rows']))
        c2.metric('Units bought', units(rep['units']))
        c3.metric('Value', inr_short(rep['value']))
        c4.metric('Blended cost', inr(rep['cost_per_unit']) + ' / unit')
        st.caption(f"{rep['supplier']} · {rep['date_min']:%d %b %Y} → {rep['date_max']:%d %b %Y}"
                   + (f" · {rep['credit_notes']} credit note(s) netted off" if rep['credit_notes'] else ''))
        st.dataframe(df, hide_index=True, width='stretch', column_config={
            'purchase_date': st.column_config.DateColumn('Date', format='DD MMM YYYY'),
            'qty': st.column_config.NumberColumn('Units', format='%d'),
            'value': st.column_config.NumberColumn('Value ₹', format='%d'),
            'gross_total': st.column_config.NumberColumn('Incl GST ₹', format='%d')})
        if st.button(f'Load {len(df):,} invoices', key=f'btn_pur_{f.name}', type='primary'):
            if TO_SUPABASE:
                from psycopg2.extras import execute_values
                cols = ['purchase_date', 'supplier', 'voucher_type', 'voucher_no', 'qty',
                        'value', 'gross_total', 'fy']
                conn = db.get_conn()
                with conn.cursor() as cur:
                    execute_values(cur, f"""INSERT INTO fact_purchase ({', '.join(cols)}) VALUES %s
                        ON CONFLICT (supplier, voucher_no, purchase_date) DO UPDATE SET
                        qty=EXCLUDED.qty, value=EXCLUDED.value, gross_total=EXCLUDED.gross_total,
                        voucher_type=EXCLUDED.voucher_type, fy=EXCLUDED.fy""",
                        [tuple(r) for r in df.reindex(columns=cols).itertuples(index=False, name=None)])
                    cur.execute("INSERT INTO sync_log (source, rows_written, status, message) "
                                "VALUES ('purchases', %s, 'ok', %s)", (len(df), f.name))
                conn.commit()
            else:
                store.append('purchase', df, ['supplier', 'voucher_no', 'purchase_date'], note=f.name)
            data.clear_cache()
            st.success(f'{len(df):,} invoices loaded. The Marketplace P&L now has a cost basis.')
            st.balloons()


# ---------------------------------------------------------------- 2 & 3. sheets
def _sheet_block(title: str, cleaner, writer, table: str, dedupe: list[str], hint: str):
    st.markdown(f'#### {title}')
    st.caption(hint)
    f = st.file_uploader('Excel or CSV', type=['xlsx', 'xls', 'csv'], key=f'up_{table}')
    if not f:
        return
    raw = pd.read_csv(f) if f.name.lower().endswith('.csv') else pd.read_excel(
        f, sheet_name=st.selectbox('Sheet / tab', pd.ExcelFile(f).sheet_names, key=f'tab_{table}'))
    st.caption(f'{len(raw):,} rows · headers: {", ".join(map(str, raw.columns))}')
    clean, missing = cleaner(raw)
    if missing:
        st.error(f'Could not find a column for **{", ".join(missing)}**. Rename the header in the '
                 f'sheet, or add its name to sheets.py.')
        return
    st.success(f'Recognised {len(clean):,} rows · columns: {", ".join(clean.columns)}')
    st.dataframe(clean.head(20), hide_index=True, width='stretch')
    if st.button(f'Load {len(clean):,} rows', key=f'btn_{table}', type='primary'):
        if TO_SUPABASE:
            n = writer(db.get_conn(), clean)
            st.success(f'{n:,} rows written to Supabase.')
        else:
            if table == 'sku':
                old = store.load('sku')
                if old is not None:
                    clean = pd.concat([old[~old['sku_id'].isin(clean['sku_id'])], clean], ignore_index=True)
                store.save('sku', clean, note=f.name)
            else:
                store.append(table, clean, dedupe, note=f.name)
            st.success('Saved locally.')
        data.clear_cache()


with t2:
    _sheet_block('Master sheet → SKU costs and style codes', sheets.clean_master, sheets.write_master,
                 'sku', ['sku_id'],
                 'This is what unlocks profit: cost price per SKU. It also adds style code and size, '
                 'which the Unicommerce export does not contain.')
with t3:
    _sheet_block('Production sheet → open lots', sheets.clean_production, sheets.write_production,
                 'production', ['lot_id'],
                 'Lot number, style, vendor, quantity, dates and stage.')


# ---------------------------------------------------------------- 4. stock
with t4:
    st.markdown('#### Stock on hand')
    st.caption('Unicommerce → Inventory → export. Needs a SKU column, a quantity column, and '
               'ideally a facility / warehouse column.')
    f = st.file_uploader('Stock export', type=['xlsx', 'xls', 'csv'], key='up_stock')
    if f:
        raw = pd.read_csv(f) if f.name.lower().endswith('.csv') else pd.read_excel(f)
        mapped, missing = sheets.map_columns(raw, {
            'sku_id': ['sku', 'sku code', 'item sku', 'seller sku', 'product sku code', 'ean'],
            'warehouse_id': ['facility', 'facility code', 'warehouse', 'godown', 'location'],
            'stock_qty': ['quantity', 'qty', 'available', 'available qty', 'inventory', 'stock'],
        }, ['sku_id', 'stock_qty'])
        if missing:
            st.error(f'Could not find: {", ".join(missing)}. Headers seen: {", ".join(map(str, raw.columns))}')
        else:
            mapped['sku_id'] = mapped['sku_id'].astype(str).str.strip()
            mapped['stock_qty'] = pd.to_numeric(mapped['stock_qty'], errors='coerce').fillna(0).astype(int)
            if 'warehouse_id' not in mapped:
                mapped['warehouse_id'] = 'Main'
            mapped['warehouse_id'] = mapped['warehouse_id'].fillna('Main').astype(str).str.strip()
            mapped['snapshot_date'] = pd.Timestamp(date.today())
            g = mapped.groupby(['snapshot_date', 'sku_id', 'warehouse_id'], as_index=False)['stock_qty'].sum()
            st.success(f'{len(g):,} SKU × warehouse rows · {g["stock_qty"].sum():,} units')
            st.dataframe(g.head(20), hide_index=True, width='stretch')
            if st.button(f'Load stock snapshot for {date.today():%d %b %Y}', type='primary'):
                if TO_SUPABASE:
                    from psycopg2.extras import execute_values
                    conn = db.get_conn()
                    with conn.cursor() as cur:
                        cur.execute('DELETE FROM fact_stock_snapshot WHERE snapshot_date = %s', (date.today(),))
                        execute_values(cur, 'INSERT INTO fact_stock_snapshot (snapshot_date, sku_id, '
                                            'warehouse_id, stock_qty) VALUES %s',
                                       [(date.today(), r.sku_id, r.warehouse_id, int(r.stock_qty))
                                        for r in g.itertuples()], page_size=1000)
                        cur.execute("INSERT INTO sync_log (source, rows_written, status) "
                                    "VALUES ('stock_upload', %s, 'ok')", (len(g),))
                    conn.commit()
                else:
                    store.save('stock', g, note=f.name)
                data.clear_cache()
                st.success('Stock loaded.')


# ---------------------------------------------------------------- 5. status
with t5:
    st.markdown('#### What the dashboard is reading right now')
    st.markdown(f'**Source:** `{data.source()}`')
    log = data.load_sync_log()
    if len(log):
        st.dataframe(log, hide_index=True, width='stretch')
    else:
        st.caption('Nothing loaded yet.')

    sku = data.load_skus()
    if not sku.empty:
        has_cost = pd.to_numeric(sku.get('cost_price'), errors='coerce').notna().sum()
        c1, c2, c3 = st.columns(3)
        c1.metric('SKUs known', units(len(sku)))
        c2.metric('With a cost price', units(has_cost))
        c3.metric('With a style code', units(sku.get('style_code', pd.Series(dtype=str)).notna().sum()))
    basis = ui.cost_basis()
    st.markdown(f'**Cost basis for the P&L:** {basis["label"]}')
    if basis['mode'] == 'none':
        st.warning('No cost data, so the P&L can only show revenue after marketplace deductions. '
                   'Load a supplier ledger on the Purchases tab for a blended cost per unit.', icon='🧾')
    elif basis['mode'] == 'blended':
        st.info('Blended cost is an average across everything bought, so it is a fair total but not a '
                'fair per-style number. A jacket and a t-shirt are charged the same. Per-style profit '
                'needs a cost price per barcode in the master sheet.', icon='ℹ️')

    if data.source() == 'local':
        st.divider()
        st.markdown('**Start over**')
        st.caption('Removes the locally stored files. Uploaded originals are untouched.')
        if st.button('Clear all local data'):
            store.clear()
            data.clear_cache()
            st.rerun()
