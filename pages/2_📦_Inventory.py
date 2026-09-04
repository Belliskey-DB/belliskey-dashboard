"""Inventory — what is in stock, how long it lasts, and what is stuck."""
from datetime import date, timedelta
import pandas as pd
import streamlit as st

import ui
import data
from fmt import inr, inr_short, units

ui.page_setup('Inventory', '📦')

VELOCITY_DAYS = 30
stock = data.load_stock_latest()
sales = data.load_sales(date.today() - timedelta(days=VELOCITY_DAYS - 1), date.today())
sold = sales[~sales['return_flag']]

if stock.empty:
    st.info('**No stock loaded yet.** Upload a Unicommerce inventory export on the 📥 Data Hub page '
            '(Stock tab) and this page fills in: units and value on hand, days of cover per style, '
            'low-cover alerts and dead stock.', icon='📦')
    st.stop()

with st.sidebar:
    st.markdown('**Filters**')
    whs = sorted(stock['warehouse_name'].unique())
    pick_wh = st.multiselect('Warehouse', whs, default=whs)
    cats = sorted(stock['category'].dropna().unique())
    pick_cat = st.multiselect('Category', cats, default=cats)
    low_cover = st.number_input('Low cover threshold (days)', 5, 60, 14)
    dead_days = st.number_input('Dead stock: no sale for (days)', 30, 180, 60)

stock = stock[stock['warehouse_name'].isin(pick_wh) & stock['category'].isin(pick_cat)]
stock = stock[stock['stock_qty'] > 0]
if stock.empty:
    st.warning('No stock rows match.')
    st.stop()

stock['at_cost'] = stock['stock_qty'] * stock['cost_price'].fillna(0)
stock['at_mrp'] = stock['stock_qty'] * stock['mrp'].fillna(0)

as_of = stock.groupby('warehouse_name')['snapshot_date'].max()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Units on hand', units(stock['stock_qty'].sum()))
c2.metric('Stock at cost', inr_short(stock['at_cost'].sum()),
          help='stock × cost_price from the Master sheet. Missing costs count as ₹0.')
c3.metric('Stock at MRP', inr_short(stock['at_mrp'].sum()))
c4.metric('SKUs in stock', units(stock['sku_id'].nunique()))
c5.metric('Styles in stock', units(stock['style_code'].nunique()))
st.caption('Snapshot dates — ' + ' · '.join(f'{w}: {d:%d %b}' for w, d in as_of.items()))
missing_cost = stock.loc[stock['cost_price'].isna(), 'sku_id'].nunique()
if missing_cost:
    st.warning(f'{missing_cost} SKUs in stock have no cost price in the Master sheet — stock-at-cost is understated.')

# ---------------------------------------------------------------- by warehouse / category
left, right = st.columns(2)
with left:
    by_wh = stock.groupby('warehouse_name', as_index=False).agg(units=('stock_qty', 'sum'), at_cost=('at_cost', 'sum'))
    fig = ui.bar(by_wh, 'warehouse_name', 'units', 'Units by warehouse', text=by_wh['units'].map(units))
    st.plotly_chart(fig, use_container_width=True)
with right:
    by_cat = stock.groupby('category', as_index=False).agg(units=('stock_qty', 'sum'), at_cost=('at_cost', 'sum')) \
                  .sort_values('units', ascending=False)
    fig = ui.bar(by_cat, 'category', 'units', 'Units by category', horizontal=True, text=by_cat['units'].map(units))
    fig.update_layout(yaxis=dict(autorange='reversed'), height=max(240, 32 * len(by_cat) + 60))
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- cover by style
st.subheader('Days of cover by style')
st.caption(f'Cover = stock ÷ average daily units sold over the last {VELOCITY_DAYS} days (returns not deducted).')
st_style = stock.groupby(['style_code', 'product_name', 'category'], as_index=False).agg(
    stock=('stock_qty', 'sum'), at_cost=('at_cost', 'sum'))
vel = sold.groupby('style_code')['qty'].sum() / VELOCITY_DAYS
last_sale = sold.groupby('style_code')['sale_date'].max()
st_style['per_day'] = st_style['style_code'].map(vel).fillna(0)
st_style['cover_days'] = (st_style['stock'] / st_style['per_day'].replace(0, pd.NA)).astype(float)
st_style['last_sale'] = st_style['style_code'].map(last_sale)
st_style['days_since_sale'] = (pd.Timestamp(date.today()) - st_style['last_sale']).dt.days

sold_style = sold.groupby('style_code')['qty'].sum()
stockout = sold_style[~sold_style.index.isin(stock['style_code'])]

tab1, tab2, tab3, tab4 = st.tabs([
    f'⚠️ Low cover (< {low_cover}d)', f'🧊 Dead stock (no sale {dead_days}d+)',
    f'❌ Selling but out of stock ({len(stockout)})', 'All styles'])

cfg = {
    'style_code': 'Style', 'product_name': 'Name', 'category': 'Category',
    'stock': st.column_config.NumberColumn('Stock', format='%d'),
    'per_day': st.column_config.NumberColumn('Units/day', format='%.1f'),
    'cover_days': st.column_config.NumberColumn('Cover (days)', format='%.0f'),
    'at_cost': st.column_config.NumberColumn('Value at cost ₹', format='%d'),
    'days_since_sale': st.column_config.NumberColumn('Days since last sale', format='%d'),
}
cols = ['style_code', 'product_name', 'category', 'stock', 'per_day', 'cover_days', 'at_cost', 'days_since_sale']

with tab1:
    low = st_style[(st_style['cover_days'] < low_cover) & (st_style['per_day'] > 0)].sort_values('cover_days')
    st.markdown(f'**{len(low)} styles** will run out within {low_cover} days at current speed — reorder or move stock.')
    st.dataframe(low[cols], hide_index=True, width='stretch', column_config=cfg)
with tab2:
    dead = st_style[(st_style['days_since_sale'].isna()) | (st_style['days_since_sale'] >= dead_days)] \
        .sort_values('at_cost', ascending=False)
    st.markdown(f'**{len(dead)} styles**, {inr(dead["at_cost"].sum())} at cost, have not sold in {dead_days}+ days — candidates for markdown or bundling.')
    st.dataframe(dead[cols], hide_index=True, width='stretch', column_config=cfg)
with tab3:
    so = pd.DataFrame({'style_code': stockout.index, f'units sold last {VELOCITY_DAYS}d': stockout.values}) \
        .sort_values(f'units sold last {VELOCITY_DAYS}d', ascending=False)
    st.markdown('Styles that sold in the window but have **zero stock** in the selected warehouses.')
    st.dataframe(so, hide_index=True, width='stretch')
with tab4:
    st.dataframe(st_style[cols].sort_values('at_cost', ascending=False), hide_index=True,
                 width='stretch', column_config=cfg)
    ui.download(st_style, f'inventory_by_style_{date.today()}.csv')

# ---------------------------------------------------------------- size-level drill
with st.expander('Drill into one style (stock by size × warehouse)'):
    pick = st.selectbox('Style', sorted(stock['style_code'].unique()))
    one = stock[stock['style_code'] == pick]
    pv = one.pivot_table(index=['color', 'size'], columns='warehouse_name', values='stock_qty',
                         aggfunc='sum', fill_value=0)
    pv['Total'] = pv.sum(axis=1)
    st.dataframe(pv, width='stretch')
