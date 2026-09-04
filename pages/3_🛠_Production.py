"""Production — open lots from the Production sheet."""
from datetime import date
import pandas as pd
import streamlit as st

import ui
import data
from fmt import units

ui.page_setup('Production', '🛠')

STAGE_ORDER = ['planning', 'fabric', 'cutting', 'stitching', 'finishing', 'received']
lots = data.load_production()
if lots.empty:
    st.info('**No production sheet loaded yet.** Upload it on the 📥 Data Hub page and this page shows '
            'open lots, units still to receive, overdue lots by vendor, and which styles are being made '
            'that are not selling.', icon='🛠')
    st.stop()

lots['current_stage'] = lots['current_stage'].fillna('planning').str.lower()
lots['open_qty'] = (lots['planned_qty'].fillna(0) - lots['received_qty'].fillna(0)).clip(lower=0)
lots['is_open'] = lots['open_qty'] > 0
today = pd.Timestamp(date.today())
lots['days_open'] = (today - lots['po_date']).dt.days
lots['overdue_days'] = (today - lots['expected_date']).dt.days.where(lots['is_open'])

with st.sidebar:
    st.markdown('**Filters**')
    vendors = sorted(lots['vendor'].dropna().unique())
    pick_v = st.multiselect('Vendor', vendors, default=vendors)
    show_closed = st.checkbox('Include fully received lots', value=False)
lots = lots[lots['vendor'].isin(pick_v)]
view = lots if show_closed else lots[lots['is_open']]

open_lots = lots[lots['is_open']]
overdue = open_lots[open_lots['overdue_days'] > 0]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Open lots', units(len(open_lots)))
c2.metric('Units planned (open)', units(open_lots['planned_qty'].sum()))
c3.metric('Units still to receive', units(open_lots['open_qty'].sum()))
c4.metric('Overdue lots', units(len(overdue)), delta=f'{units(overdue["open_qty"].sum())} units late',
          delta_color='inverse' if len(overdue) else 'off')
c5.metric('Vendors active', units(open_lots['vendor'].nunique()))
if lots['updated_at'].notna().any():
    st.caption(f'Sheet last synced {lots["updated_at"].max():%d %b %Y %H:%M}')

left, right = st.columns(2)
with left:
    by_stage = open_lots.groupby('current_stage', as_index=False).agg(units=('open_qty', 'sum'), lots=('lot_id', 'count'))
    by_stage['current_stage'] = pd.Categorical(by_stage['current_stage'], STAGE_ORDER, ordered=True)
    by_stage = by_stage.sort_values('current_stage')
    fig = ui.bar(by_stage, 'current_stage', 'units', 'Open units by stage', text=by_stage['units'].map(units))
    st.plotly_chart(fig, use_container_width=True)
with right:
    by_v = open_lots.groupby('vendor', as_index=False).agg(units=('open_qty', 'sum'), lots=('lot_id', 'count'),
                                                          overdue=('overdue_days', lambda s: (s > 0).sum()))
    by_v = by_v.sort_values('units', ascending=False)
    fig = ui.bar(by_v, 'vendor', 'units', 'Open units by vendor', horizontal=True, text=by_v['units'].map(units))
    fig.update_layout(yaxis=dict(autorange='reversed'))
    st.plotly_chart(fig, use_container_width=True)

st.subheader('Lots')
cfg = {
    'lot_id': 'Lot', 'style_code': 'Style', 'product_name': 'Name', 'category': 'Category', 'vendor': 'Vendor',
    'color': 'Colour', 'current_stage': 'Stage',
    'planned_qty': st.column_config.NumberColumn('Planned', format='%d'),
    'received_qty': st.column_config.NumberColumn('Received', format='%d'),
    'open_qty': st.column_config.NumberColumn('To receive', format='%d'),
    'po_date': st.column_config.DateColumn('PO date', format='DD MMM'),
    'expected_date': st.column_config.DateColumn('Expected', format='DD MMM'),
    'days_open': st.column_config.NumberColumn('Days open', format='%d'),
    'overdue_days': st.column_config.NumberColumn('Days late', format='%d'),
}
cols = ['lot_id', 'style_code', 'product_name', 'category', 'vendor', 'current_stage', 'planned_qty',
        'received_qty', 'open_qty', 'po_date', 'expected_date', 'days_open', 'overdue_days']
tab1, tab2 = st.tabs([f'⏰ Overdue ({len(overdue)})', f'All ({len(view)})'])
with tab1:
    st.dataframe(overdue[cols].sort_values('overdue_days', ascending=False), hide_index=True,
                 use_container_width=True, column_config=cfg)
with tab2:
    st.dataframe(view[cols].sort_values('expected_date'), hide_index=True, use_container_width=True, column_config=cfg)
    ui.download(view[cols], f'production_lots_{date.today()}.csv')

with st.expander('Sales vs production — is what we are making what is selling?'):
    from datetime import timedelta
    sales = data.load_sales(date.today() - timedelta(days=29), date.today())
    sold = sales[~sales['return_flag']].groupby('style_code')['qty'].sum().rename('sold_30d')
    stock = data.load_stock_latest().groupby('style_code')['stock_qty'].sum().rename('stock')
    prod = open_lots.groupby('style_code')['open_qty'].sum().rename('in_production')
    cmp = pd.concat([sold, stock, prod], axis=1).fillna(0)
    cmp = cmp[(cmp['sold_30d'] > 0) | (cmp['in_production'] > 0)]
    cmp['cover_after_production_days'] = ((cmp['stock'] + cmp['in_production']) / (cmp['sold_30d'] / 30).replace(0, pd.NA)).astype(float)
    st.markdown('**Making but not selling** — in production, zero sales in 30 days:')
    st.dataframe(cmp[(cmp['in_production'] > 0) & (cmp['sold_30d'] == 0)].sort_values('in_production', ascending=False), use_container_width=True)
    st.markdown('**Selling but not making** — top sellers with under 30 days cover and nothing in production:')
    st.dataframe(cmp[(cmp['in_production'] == 0) & (cmp['cover_after_production_days'] < 30)].sort_values('sold_30d', ascending=False).head(30), use_container_width=True)
