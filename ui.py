"""ui.py — small shared bits so every page looks and behaves the same."""
from __future__ import annotations

from datetime import date, timedelta
import pandas as pd
import plotly.express as px
import plotly.io as pio
import streamlit as st

from auth import require_auth
import data


PALETTE = ['#2E5AAC', '#E8772E', '#2A9D8F', '#C8385A', '#8E6BBF', '#6C757D', '#F2C14E']
CHANNEL_COLOURS = {
    'Myntra': '#E8398D', 'Amazon': '#FF9900', 'Flipkart': '#2874F0',
    'Ajio': '#1F1F1F', 'Nykaa': '#FC2779', 'Other': '#6C757D',
}

pio.templates.default = 'plotly_white'
px.defaults.color_discrete_sequence = PALETTE
px.defaults.height = 360


def page_setup(title: str, icon: str) -> None:
    """set_page_config + auth gate + data-source banner. Call first on every page."""
    try:
        brand = st.secrets.get('BRAND_NAME', 'Marketplace Dashboard')
    except Exception:
        brand = 'Marketplace Dashboard'
    st.set_page_config(page_title=f'{title} — {brand}', page_icon=icon, layout='wide',
                       initial_sidebar_state='expanded')
    require_auth()
    st.title(f'{icon} {title}')
    src = data.source()
    if src == 'demo':
        st.info('**Demo data.** Load a real file on the 📥 Data Hub page, or connect Supabase. '
                'Every chart below works identically on real data.', icon='🧪')
    elif src == 'local':
        log = data.load_sync_log()
        bits = [f"{r.source}: {r.rows_written:,} rows" for r in log.itertuples()] if len(log) else []
        st.caption('Reading locally uploaded files — ' + ' · '.join(bits) +
                   '  ·  connect Supabase to make this permanent.')
    else:
        log = data.load_sync_log()
        if len(log):
            bits = [f"{r.source.replace('_', ' ')}: {pd.Timestamp(r.ran_at).strftime('%d %b %H:%M')}"
                    for r in log.itertuples()]
            st.caption('Data as of — ' + ' · '.join(bits))
    with st.sidebar:
        if st.button('🔄 Refresh data', width='stretch'):
            data.clear_cache()
            st.rerun()


def sidebar_period(default_days: int = 30, key: str = 'period') -> tuple[date, date]:
    """Date-range picker in the sidebar. Defaults to everything that is loaded."""
    today = date.today()
    lo, hi = data.date_bounds()
    options = ['All loaded data', 'Last 7 days', 'Last 30 days', 'Last 90 days', 'This month', 'Custom']
    if lo is None:
        options = options[1:]
    with st.sidebar:
        st.markdown('**Period**')
        choice = st.radio('Period', options, index=0, label_visibility='collapsed', key=f'{key}_preset')
        if choice == 'All loaded data':
            start, end = lo, hi
        elif choice == 'This month':
            start, end = today.replace(day=1), today
        elif choice == 'Custom':
            base = (lo or today - timedelta(days=default_days), hi or today)
            rng = st.date_input('Range', base, key=f'{key}_custom')
            start, end = (rng[0], rng[1]) if isinstance(rng, tuple) and len(rng) == 2 else base
        else:
            days = {'Last 7 days': 7, 'Last 30 days': 30, 'Last 90 days': 90}[choice]
            start, end = today - timedelta(days=days - 1), today
        if lo is not None:
            st.caption(f'Data available {lo:%d %b %Y} → {hi:%d %b %Y}')
    return start, end


def sidebar_channels(df: pd.DataFrame, key: str = 'channels') -> pd.DataFrame:
    names = sorted(df['channel_name'].dropna().unique().tolist())
    with st.sidebar:
        picked = st.multiselect('Channels', names, default=names, key=key)
    return df[df['channel_name'].isin(picked)] if picked else df


def kpi(label: str, value: str, help: str | None = None, delta: str | None = None) -> None:
    st.metric(label, value, delta=delta, help=help)


def bar(df: pd.DataFrame, x: str, y: str, title: str = '', color: str | None = None,
        text: str | None = None, horizontal: bool = False, colour_map: dict | None = None):
    fig = px.bar(df, x=y if horizontal else x, y=x if horizontal else y, color=color, text=text,
                 title=title, orientation='h' if horizontal else 'v',
                 color_discrete_map=colour_map or {})
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), legend_title_text='',
                      xaxis_title='', yaxis_title='')
    if text is not None:
        fig.update_traces(textposition='outside', cliponaxis=False)
    return fig


def line(df: pd.DataFrame, x: str, y: str, title: str = '', color: str | None = None,
         colour_map: dict | None = None):
    fig = px.line(df, x=x, y=y, color=color, title=title, color_discrete_map=colour_map or {})
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10), legend_title_text='',
                      xaxis_title='', yaxis_title='', hovermode='x unified')
    return fig


def download(df: pd.DataFrame, name: str, label: str = '⬇️ Download CSV') -> None:
    st.download_button(label, df.to_csv(index=False).encode(), file_name=name, mime='text/csv')
