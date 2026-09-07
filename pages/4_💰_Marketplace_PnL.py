"""
Marketplace P&L — what each channel leaves after its deductions and our cost.

Contribution per channel =
    net sales (gross − returns)
  − marketplace deductions  (deduction_pct × gross shipped value; charged on returns too — most
                             marketplaces keep reverse-shipping and part of the commission)
  − cost of goods           (cost_price × units NOT returned)
  − handling overhead       (overhead_per_unit × every piece handled: forward + reverse)

This is an ESTIMATE. It becomes exact only when the deduction rates are
replaced with numbers read off actual settlement reports.
"""
from datetime import date
import numpy as np
import pandas as pd
import streamlit as st

import ui
import data
from fmt import inr, inr_short, units, pct

ui.page_setup('Marketplace P&L', '💰')
start, end = ui.sidebar_period(30)
df = data.load_sales(start, end)
channels = data.load_channels()

with st.sidebar:
    st.markdown('**Deduction rates** (what the marketplace keeps, % of selling price)')
    st.caption('Defaults come from the database. Change here to test a scenario.')
    rates, overheads = {}, {}
    for r in channels.itertuples():
        rates[r.channel_id] = st.slider(r.channel_name, 0.0, 70.0, float(r.deduction_pct), 0.5,
                                        key=f'rate_{r.channel_id}')
    overhead_default = float(channels['overhead_per_unit'].median()) if len(channels) else 40.0
    overhead = st.number_input('Handling cost per piece (₹)', 0.0, 500.0, overhead_default, 5.0,
                               help='Packing + 3PL + office cost per piece handled. Charged on forward AND return.')

if df.empty:
    st.warning('No sales in this period. Widen the date range, or load a file on the 📥 Data Hub page.')
    st.stop()

BASIS = ui.cost_basis(start, end)
HAS_COST = BASIS['mode'] != 'none'
BLENDED = BASIS['mode'] == 'blended'

if BASIS['mode'] == 'none':
    st.warning(
        '**Cost of goods is missing, so this is not profit yet.** What you see below is revenue '
        'after the marketplace deduction and handling only. Load the supplier ledger on the '
        '📥 Data Hub page (Purchases tab) and every figure here becomes an approximate margin.',
        icon='🧾')
elif BLENDED:
    st.info(
        f'**Cost of goods is a blended {inr(BASIS["rate"])} per unit**, from the supplier ledger'
        + (f' for {BASIS["fy"]}' if BASIS['fy'] else '') +
        ' — total value bought divided by units bought. The company total is sound; the per-style '
        'and per-category splits are not, because a jacket is charged the same as a t-shirt. '
        'Per-style profit needs a cost price per barcode.', icon='⚖️')

sold = df[~df['return_flag']]
ret = df[df['return_flag']]

def _by(group_cols):
    # With no per-barcode cost, every unit carries the same blended rate.
    if BLENDED:
        sold['cost_price'] = float(BASIS['rate'])
        ret['cost_price'] = float(BASIS['rate'])
    _cost = pd.to_numeric(sold['cost_price'], errors='coerce').fillna(0.0)
    s = sold.assign(cogs=pd.to_numeric(sold['qty'], errors='coerce').fillna(0) * _cost)
    g = s.groupby(group_cols).agg(units=('qty', 'sum'), gross=('net_value', 'sum'),
                                  cogs_all=('cogs', 'sum')).reset_index()
    _rcost = pd.to_numeric(ret['cost_price'], errors='coerce').fillna(0.0)
    r = ret.assign(cogs=pd.to_numeric(ret['qty'], errors='coerce').fillna(0) * _rcost).groupby(group_cols).agg(
        ret_units=('qty', 'sum'), returns=('net_value', 'sum'), cogs_back=('cogs', 'sum')).reset_index()
    g = g.merge(r, on=group_cols, how='left')
    for _c in ('ret_units', 'returns', 'cogs_back'):
        g[_c] = pd.to_numeric(g[_c], errors='coerce').fillna(0.0)
    g['net_sales'] = g['gross'] - g['returns']
    g['rate'] = g['channel_id'].map(rates).fillna(0)
    g['deductions'] = g['gross'] * g['rate'] / 100
    g['cogs'] = g['cogs_all'] - g['cogs_back']
    g['handling'] = (g['units'] + g['ret_units']) * overhead
    g['contribution'] = g['net_sales'] - g['deductions'] - g['cogs'] - g['handling']
    g['margin_pct'] = g['contribution'] / g['net_sales'].replace(0, np.nan) * 100
    g['per_unit'] = g['contribution'] / (g['units'] - g['ret_units']).replace(0, np.nan)
    g['return_rate'] = g['ret_units'] / g['units'].replace(0, np.nan) * 100
    return g

ch = _by(['channel_id', 'channel_name']).sort_values('contribution', ascending=False)
tot = ch[['units', 'ret_units', 'gross', 'returns', 'net_sales', 'deductions', 'cogs', 'handling', 'contribution']].sum()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('Net sales', inr_short(tot['net_sales']))
c2.metric('Marketplace deductions', inr_short(tot['deductions']), f'{tot["deductions"]/tot["gross"]*100:.1f}% of gross', delta_color='off')
c3.metric('Cost of goods', inr_short(tot['cogs']), f'{tot["cogs"]/tot["net_sales"]*100:.1f}% of net' if tot['net_sales'] else None, delta_color='off')
c4.metric('Handling', inr_short(tot['handling']))
_label = 'Contribution' if HAS_COST else 'After deductions'
c5.metric(_label, inr_short(tot['contribution']),
          f'{tot["contribution"]/tot["net_sales"]*100:.1f}% of net sales' if tot['net_sales'] else None,
          help=None if HAS_COST else 'Cost of goods is NOT deducted — no cost prices loaded yet.')

if HAS_COST and not BLENDED:
    no_cost = sold.loc[sold['cost_price'].isna(), 'sku_id'].nunique()
    if no_cost:
        st.warning(f'{no_cost} sold SKUs have no cost price in the Master sheet — their cost counts as ₹0, '
                   f'so the figures above are better than reality.')

# ---------------------------------------------------------------- waterfall + per channel
left, right = st.columns([2, 3])
with left:
    import plotly.graph_objects as go
    fig = go.Figure(go.Waterfall(
        orientation='v', measure=['absolute', 'relative', 'relative', 'relative', 'relative', 'total'],
        x=['Sales', 'Returns', 'Deductions', 'Cost of goods', 'Handling',
           'Contribution' if HAS_COST else 'After deductions'],
        y=[tot['gross'], -tot['returns'], -tot['deductions'], -tot['cogs'], -tot['handling'], 0],
        text=[inr_short(v) for v in (tot['gross'], -tot['returns'], -tot['deductions'], -tot['cogs'], -tot['handling'], tot['contribution'])],
        textposition='outside', connector={'line': {'color': '#bbb'}},
        decreasing={'marker': {'color': '#C8385A'}}, increasing={'marker': {'color': '#2A9D8F'}},
        totals={'marker': {'color': '#2E5AAC'}}))
    fig.update_layout(title='Where the money goes', margin=dict(l=10, r=10, t=40, b=10), height=380, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
with right:
    fig = ui.bar(ch, 'channel_name', 'contribution',
                 'Contribution by channel' if HAS_COST else 'Left after deductions, by channel',
                 color='channel_name',
                 text=ch['contribution'].map(inr_short), colour_map=ui.CHANNEL_COLOURS)
    fig.update_layout(showlegend=False, height=380)
    st.plotly_chart(fig, use_container_width=True)

st.subheader('By channel')
st.dataframe(ch[['channel_name', 'units', 'return_rate', 'gross', 'returns', 'net_sales', 'rate', 'deductions',
                 'cogs', 'handling', 'contribution', 'margin_pct', 'per_unit']],
             hide_index=True, width='stretch', column_config={
    'channel_name': 'Channel',
    'units': st.column_config.NumberColumn('Units', format='%d'),
    'return_rate': st.column_config.NumberColumn('Return %', format='%.0f%%'),
    'gross': st.column_config.NumberColumn('Gross ₹', format='%d'),
    'returns': st.column_config.NumberColumn('Returns ₹', format='%d'),
    'net_sales': st.column_config.NumberColumn('Net ₹', format='%d'),
    'rate': st.column_config.NumberColumn('Ded. %', format='%.1f%%'),
    'deductions': st.column_config.NumberColumn('Deductions ₹', format='%d'),
    'cogs': st.column_config.NumberColumn('COGS ₹', format='%d'),
    'handling': st.column_config.NumberColumn('Handling ₹', format='%d'),
    'contribution': st.column_config.NumberColumn('Contribution ₹', format='%d'),
    'margin_pct': st.column_config.NumberColumn('Margin %', format='%.1f%%'),
    'per_unit': st.column_config.NumberColumn('₹ / net unit', format='%d'),
})
st.caption(('Contribution is before fixed costs such as salaries, rent and advertising. '
            + ('Cost of goods is blended, not per style. ' if BLENDED else '')
            if HAS_COST else
            'Cost of goods is not included yet, so treat these as revenue after deductions, not profit. ')
           + 'Deduction rates are estimates until they are checked against real settlement reports.')

st.subheader('By category × channel')
cc = _by(['channel_id', 'channel_name', 'category'])
pv = cc.pivot_table(index='category', columns='channel_name', values='margin_pct', aggfunc='first')
st.markdown('Contribution margin % — red cells lose money on that marketplace.' if HAS_COST
            else 'Share of net sales left after deductions and handling, before cost of goods.')
if BLENDED:
    st.caption('Read the CHANNEL differences here, not the category ones — every category is '
               'carrying the same blended cost, so a category that is genuinely cheap to make '
               'looks no better than an expensive one.')
def _shade(v):
    if pd.isna(v):
        return ''
    if v < 0:
        return 'background-color: #F8D7DA; color: #7A1E2B'
    if v < 10:
        return 'background-color: #FFF3CD; color: #6B4E00'
    return 'background-color: #D4EDDA; color: #155724'
st.dataframe(pv.round(1).style.map(_shade).format('{:.1f}%', na_rep='–'), width='stretch')
st.caption('Red = losing money · amber = under 10% margin · green = 10%+')

with st.expander('Style-level contribution (find loss-making styles)'):
    sty = _by(['channel_id', 'channel_name', 'style_code', 'product_name', 'category'])
    agg = sty.groupby(['style_code', 'product_name', 'category']).agg(
        units=('units', 'sum'), net_sales=('net_sales', 'sum'), contribution=('contribution', 'sum'),
        ret_units=('ret_units', 'sum')).reset_index()
    agg['margin_pct'] = agg['contribution'] / agg['net_sales'].replace(0, np.nan) * 100
    agg['return_rate'] = agg['ret_units'] / agg['units'].replace(0, np.nan) * 100
    order = st.radio('Sort', ['Lowest margin first', 'Biggest contribution first'], horizontal=True)
    agg = agg.sort_values('margin_pct' if order.startswith('Lowest') else 'contribution',
                          ascending=order.startswith('Lowest'))
    st.dataframe(agg[agg['units'] >= 5], hide_index=True, width='stretch', column_config={
        'net_sales': st.column_config.NumberColumn('Net ₹', format='%d'),
        'contribution': st.column_config.NumberColumn('Contribution ₹', format='%d'),
        'margin_pct': st.column_config.NumberColumn('Margin %', format='%.1f%%'),
        'return_rate': st.column_config.NumberColumn('Return %', format='%.0f%%'),
    })
    ui.download(agg, f'style_pnl_{start}_{end}.csv')
