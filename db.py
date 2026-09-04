"""
db.py — Supabase (Postgres) connection shared by every page and script.

Secrets (in .streamlit/secrets.toml locally, or App Secrets on Streamlit Cloud):
    SUPABASE_HOST     = "aws-0-ap-south-1.pooler.supabase.com"   # Project → Connect → Session pooler
    SUPABASE_PORT     = 5432
    SUPABASE_USER     = "postgres.<projectref>"
    SUPABASE_PASSWORD = "..."
    SUPABASE_DB       = "postgres"

If SUPABASE_HOST is missing the app runs on DEMO data (see demo_data.py).
"""
from __future__ import annotations

import os
import pandas as pd
import streamlit as st


def _secret(key: str, default=None):
    v = os.environ.get(key)
    if v:
        return v
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def is_configured() -> bool:
    return bool(_secret('SUPABASE_HOST')) and bool(_secret('SUPABASE_PASSWORD'))


def connect_kwargs() -> dict:
    return dict(
        host=_secret('SUPABASE_HOST'),
        port=int(_secret('SUPABASE_PORT', 5432)),
        user=_secret('SUPABASE_USER', 'postgres'),
        password=_secret('SUPABASE_PASSWORD'),
        database=_secret('SUPABASE_DB', 'postgres'),
        sslmode='require',
        connect_timeout=10,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
    )


@st.cache_resource(show_spinner=False)
def _conn():
    import psycopg2
    conn = psycopg2.connect(**connect_kwargs())
    conn.set_session(autocommit=True)
    return conn


def get_conn():
    """Cached connection; redials once if the socket died while idle."""
    import psycopg2
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
        return conn
    except (psycopg2.InterfaceError, psycopg2.OperationalError, psycopg2.DatabaseError):
        _conn.clear()
        return _conn()


def query_df(sql: str, params=None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def execute(sql: str, params=None) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
