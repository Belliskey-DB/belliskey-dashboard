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


def _sanitise(msg: str) -> str:
    """Never let a connection string or password reach the screen."""
    import re
    msg = re.sub(r"password=\S+", "password=***", str(msg))
    msg = re.sub(r"://[^@\s]+@", "://***@", msg)
    return msg.strip()[:300]


def health() -> str | None:
    """None when the database answers. Otherwise a short, safe reason why not."""
    if not is_configured():
        return None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
        return None
    except Exception as e:                       # noqa: BLE001 - any failure is a failure
        return _sanitise(e)


def diagnose(reason: str) -> str:
    """Turn a psycopg2 message into the thing to actually go and check."""
    r = (reason or '').lower()
    if 'could not translate host name' in r or 'name or service not known' in r or 'nodename nor servname' in r:
        return ('The host name does not resolve. Check SUPABASE_HOST — it must be copied from your own '
                'project under Connect, and it must be the **Session pooler** host, which ends in '
                '`.pooler.supabase.com`.')
    if 'password authentication failed' in r:
        return ('The host was reached but the login was rejected. Check SUPABASE_USER is the pooler user '
                '(it looks like `postgres.abcdefghijklmnop`, not plain `postgres`) and that '
                'SUPABASE_PASSWORD is the **database** password, not the anon or service key.')
    if 'timeout' in r or 'timed out' in r or 'no route to host' in r or 'network is unreachable' in r:
        return ('The connection timed out. This is almost always the **Direct connection** host being used '
                'instead of the Session pooler. Direct connections are IPv6-only and Streamlit Cloud '
                'cannot reach them. Use the Session pooler tab.')
    if 'does not exist' in r and 'database' in r:
        return 'SUPABASE_DB should be `postgres`.'
    return ('Check every SUPABASE_ value against the Session pooler tab in Supabase under Connect.')


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
