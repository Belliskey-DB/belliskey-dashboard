"""Sales — the daily view. Revenue is always shown NET of returns."""
import pandas as pd
import streamlit as st

import ui
import data
from fmt import inr, inr_short, units, pct

ui.page_setup('Sales', '📊')
start, end = ui.sidebar_period()
df = data.load_sales(start, end)
df = ui.sidebar_channels(df)

if df.empty:
    st.warning('No sales in this period. Widen the date range in the sidebar, or load a file on the '
               '📥 Data Hub page.')
    st.stop()

sold = df[~df['return_flag']]
ret = df[df['return_flag']]

gross = sold['net_value'].sum()          # what customers paid (after discount)
returns = ret['net_value'].sum()
net = gross - returns
units_sold = int(sold['qty'].sum())
units_ret = int(ret['qty'].sum())
orders = sold['order_id'].nunique()
return_rate = units_ret / units_sold * 100 if units_sold else 0
asp = gross / units_sold if units_sold else 0

# --- previous period for deltas
span = (end - start).days + 1
prev = data.load_sales(start - pd.Timedelta(days=span), start - pd.Timedelta(days=1))
prev = prev[prev['channel_name'].isin(df['channel_name'].unique())]
p_net = prev[~prev['return_flag']]['net_value'].sum() - prev[prev['return_flag']]['net_value'].sum()
p_units = int(prev[~prev['return_flag']]['qty'].sum())


def _delta(cur, old):
    if not old:
        return None
    return f'{(cur - old) / old * 100:+.0f}% vs prior {span}d'


c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric('Net sales', inr_short(net), _delta(net, p_net),
          help='Gross sales minus value of returns received in the period.')
c2.metric('Gross sales', inr_short(gross), help='Value of units shipped (after discount, before returns).')
c3.metric('Returns', inr_short(returns), f'{return_rate:.1f}% of units', delta_color='inverse')
c4.metric('Units sold', units(units_sold), _delta(units_sold, p_units))
c5.metric('Orders', units(orders))
c6.metric('Avg selling price', inr(asp))

st.caption(f'{start:%d %b %Y} → {end:%d %b %Y} · sales {inr(gross)} − returns {inr(returns)} = **net {inr(net)}**')
if ret.empty and not sold.empty:
    st.warning('No return rows in this period, so every figure here is **before returns**. '
               'Load the Unicommerce return export on the 📥 Data Hub page to see the real net.', icon='↩️')

# ---------------------------------------------------------------- trend + channel mix
left, right = st.columns([3, 2])
daily = (df.assign(signed=df['net_value'] * df['return_flag'].map({False: 1, True: -1}))
           .groupby('sale_date', as_index=False)['signed'].sum()
           .rename(columns={'signed': 'Net sales'}))
daily['7-day avg'] = daily['Net sales'].rolling(7, min_periods=1).mean()
with left:
    fig = ui.line(daily.melt('sale_date', var_name='series', value_name='₹'), 'sale_date', '₹',
                  'Net sales per day', color='series',
                  colour_map={'Net sales': '#B8C4DE', '7-day avg': '#2E5AAC'})
    st.plotly_chart(fig, use_container_width=True)

by_ch = (df.groupby(['channel_name', 'return_flag'])['net_value'].sum().unstack(fill_value=0)
           .rename(columns={False: 'gross', True: 'returns'}))
for _c in ('gross', 'returns'):          # a period with no returns has only one column
    if _c not in by_ch:
        by_ch[_c] = 0.0
by_ch['net'] = by_ch['gross'] - by_ch['returns']
by_ch['units'] = sold.groupby('channel_name')['qty'].sum()
by_ch['ret_units'] = ret.groupby('channel_name')['qty'].sum()
by_ch = by_ch.fillna(0)
by_ch['return_rate'] = (by_ch['ret_units'] / by_ch['units'].replace(0, pd.NA) * 100).fillna(0)
by_ch = by_ch.sort_values('net', ascending=False).reset_index()
with right:
    fig = ui.bar(by_ch, 'channel_name', 'net', 'Net sales by channel', color='channel_name',
                 text=by_ch['net'].map(inr_short), colour_map=ui.CHANNEL_COLOURS)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- channel table + category
left, right = st.columns(2)
with left:
    st.subheader('By channel')
    show = by_ch[['channel_name', 'units', 'gross', 'returns', 'net', 'return_rate']].copy()
    show['share'] = show['net'] / show['net'].sum() * 100
    st.dataframe(show, hide_index=True, use_container_width=True, column_config={
        'channel_name': 'Channel', 'units': st.column_config.NumberColumn('Units', format='%d'),
        'gross': st.column_config.NumberColumn('Gross ₹', format='%d'),
        'returns': st.column_config.NumberColumn('Returns ₹', format='%d'),
        'net': st.column_config.NumberColumn('Net ₹', format='%d'),
        'return_rate': st.column_config.NumberColumn('Return %', format='%.1f%%'),
        'share': st.column_config.NumberColumn('Share of net', format='%.1f%%'),
    })
with right:
    st.subheader('By category')
    by_cat = (df.assign(signed=df['net_value'] * df['return_flag'].map({False: 1, True: -1}))
                .groupby('category', as_index=False).agg(net=('signed', 'sum')))
    by_cat['units'] = by_cat['category'].map(sold.groupby('category')['qty'].sum()).fillna(0)
    by_cat = by_cat.sort_values('net', ascending=False)
    fig = ui.bar(by_cat, 'category', 'net', '', horizontal=True, text=by_cat['net'].map(inr_short))
    fig.update_layout(yaxis=dict(autorange='reversed'), height=max(240, 32 * len(by_cat) + 60))
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- top styles
st.subheader('Top styles')
if sold['style_code'].equals(sold['sku_id']):
    st.caption('Each row is one barcode, because the Unicommerce export has no style code. '
               'Once the Master sheet is loaded these group into real styles across sizes.')
n = st.slider('Show top', 10, 100, 25, 5, key='top_n')
g = sold.groupby(['style_code', 'product_name', 'category']).agg(
    units=('qty', 'sum'), gross=('net_value', 'sum'), orders=('order_id', 'nunique')).reset_index()
r = ret.groupby('style_code').agg(ret_units=('qty', 'sum'), ret_value=('net_value', 'sum'))
g = g.merge(r, on='style_code', how='left').fillna({'ret_units': 0, 'ret_value': 0})
g['net'] = g['gross'] - g['ret_value']
g['return_rate'] = g['ret_units'] / g['units'] * 100
g['asp'] = g['gross'] / g['units']
top = g.sort_values('net', ascending=False).head(n)
st.dataframe(top[['style_code', 'product_name', 'category', 'units', 'net', 'asp', 'return_rate']],
             hide_index=True, use_container_width=True, column_config={
    'style_code': 'Style', 'product_name': 'Name', 'category': 'Category',
    'units': st.column_config.NumberColumn('Units', format='%d'),
    'net': st.column_config.NumberColumn('Net ₹', format='%d'),
    'asp': st.column_config.NumberColumn('ASP ₹', format='%d'),
    'return_rate': st.column_config.ProgressColumn('Return %', format='%.0f%%', min_value=0, max_value=100),
})
ui.download(g.sort_values('net', ascending=False), f'styles_{start}_{end}.csv', 'Download all styles')

# ---------------------------------------------------------------- where and how
st.subheader('Where it sells and how it is paid')
gcol, pcol = st.columns([3, 2])

with gcol:
    if sold['state'].notna().any() and sold['state'].astype(str).str.strip().ne('').any():
        geo = (sold[sold['state'].astype(str).str.strip().ne('')]
               .groupby('state', as_index=False).agg(units=('qty', 'sum'), value=('net_value', 'sum'))
               .sort_values('value', ascending=False).head(12))
        fig = ui.bar(geo, 'state', 'value', 'Top states by sales', horizontal=True,
                     text=geo['value'].map(inr_short))
        fig.update_layout(yaxis=dict(autorange='reversed'), height=max(280, 28 * len(geo) + 60))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption('No delivery state on these rows.')

with pcol:
    if sold['payment_method'].notna().any() and sold['payment_method'].astype(str).str.strip().ne('').any():
        pay = (sold[sold['payment_method'].astype(str).str.strip().ne('')]
               .groupby('payment_method', as_index=False).agg(units=('qty', 'sum'), value=('net_value', 'sum')))
        pay['share'] = pay['value'] / pay['value'].sum() * 100
        fig = ui.bar(pay, 'payment_method', 'value', 'Cash on delivery vs prepaid',
                     text=pay.apply(lambda r: f"{inr_short(r['value'])} · {r['share']:.0f}%", axis=1))
        fig.update_layout(showlegend=False, height=300)
        st.plotly_chart(fig, use_container_width=True)
        cod = pay.loc[pay['payment_method'].str.contains('COD', na=False), 'share'].sum()
        if cod:
            st.caption(f'{cod:.0f}% of sales value is cash on delivery. COD orders are returned far more '
                       f'often than prepaid, so this is the single biggest driver of the return rate.')
    else:
        st.caption('No payment method on these rows.')

with st.expander('Top cities'):
    if sold['city'].astype(str).str.strip().ne('').any():
        cities = (sold[sold['city'].astype(str).str.strip().ne('')]
                  .groupby(['city', 'state'], as_index=False)
                  .agg(units=('qty', 'sum'), value=('net_value', 'sum'))
                  .sort_values('value', ascending=False).head(40))
        st.dataframe(cities, hide_index=True, use_container_width=True, column_config={
            'city': 'City', 'state': 'State',
            'units': st.column_config.NumberColumn('Units', format='%d'),
            'value': st.column_config.NumberColumn('Sales ₹', format='%d')})
        ui.download(cities, f'cities_{start}_{end}.csv')

# ---------------------------------------------------------------- discount depth
if sold['discount'].notna().any() and float(pd.to_numeric(sold['discount'], errors='coerce').sum()) > 0:
    with st.expander('Discount depth (how far below MRP things are selling)'):
        d = sold.copy()
        d['mrp_value'] = pd.to_numeric(d['gross_value'], errors='coerce')
        d['paid'] = pd.to_numeric(d['net_value'], errors='coerce')
        by = d.groupby('category', as_index=False).agg(mrp_value=('mrp_value', 'sum'),
                                                       paid=('paid', 'sum'), units=('qty', 'sum'))
        by['discount_pct'] = (1 - by['paid'] / by['mrp_value'].replace(0, pd.NA)) * 100
        by = by.sort_values('paid', ascending=False)
        overall = (1 - by['paid'].sum() / by['mrp_value'].sum()) * 100
        st.markdown(f'Overall, things sell at **{overall:.0f}% below MRP**.')
        st.dataframe(by[['category', 'units', 'mrp_value', 'paid', 'discount_pct']],
                     hide_index=True, use_container_width=True, column_config={
            'category': 'Category',
            'units': st.column_config.NumberColumn('Units', format='%d'),
            'mrp_value': st.column_config.NumberColumn('Value at MRP ₹', format='%d'),
            'paid': st.column_config.NumberColumn('Actually paid ₹', format='%d'),
            'discount_pct': st.column_config.NumberColumn('Discount off MRP', format='%.0f%%')})
        st.caption('MRP here is what the Unicommerce export implies: amount paid plus the discount '
                   'recorded on the line.')

# ---------------------------------------------------------------- size curve
with st.expander('Size curve (units sold by size, per category)'):
    if sold['size'].isna().all() or sold['size'].astype(str).str.strip().eq('').all():
        st.info('No sizes on these rows. The Unicommerce export identifies each item only by its '
                'barcode. Load the Master sheet on the 📥 Data Hub page to get size and style code, '
                'and this fills in.', icon='📗')
        st.stop()
    sc = sold.pivot_table(index='category', columns='size', values='qty', aggfunc='sum', fill_value=0)
    order = [s for s in ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL'] if s in sc.columns] + \
            [s for s in sc.columns if s not in ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL']]
    sc = sc[order]
    st.dataframe((sc.div(sc.sum(axis=1), axis=0) * 100).round(0).astype(int).astype(str) + '%',
                 use_container_width=True)
