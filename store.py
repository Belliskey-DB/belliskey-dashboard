"""
store.py — a local file store, so uploaded data renders immediately.

Uploads land here as Parquet under ./data. The dashboard reads them whenever
Supabase is not configured, which means a file can be uploaded and looked at
on the same day, before any database exists.

When Supabase IS configured the same uploads go to Postgres instead and this
directory is ignored. Nothing here is a substitute for the database: it lives
on one machine and does not survive a Streamlit Cloud restart.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
TABLES = ('sales', 'sku', 'stock', 'production')


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, f'{name}.parquet')


def _meta_path() -> str:
    return os.path.join(DATA_DIR, 'meta.json')


def is_ephemeral() -> bool:
    """True on Streamlit Cloud, where the app directory is wiped on every restart."""
    return DATA_DIR.startswith('/mount/src') or bool(os.environ.get('STREAMLIT_SHARING_MODE'))


def has_any() -> bool:
    return any(os.path.exists(_path(t)) for t in TABLES)


def has(name: str) -> bool:
    return os.path.exists(_path(name))


def save(name: str, df: pd.DataFrame, note: str = '') -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_parquet(_path(name), index=False)
    meta = read_meta()
    meta[name] = {'rows': len(df), 'saved_at': datetime.now().isoformat(timespec='seconds'), 'note': note}
    with open(_meta_path(), 'w') as f:
        json.dump(meta, f, indent=2)
    return len(df)


def append(name: str, df: pd.DataFrame, dedupe_on: list[str], note: str = '') -> tuple[int, int]:
    """Add rows to a table, replacing any that match on dedupe_on. Returns (added, total)."""
    old = load(name)
    if old is None or old.empty:
        return len(df), save(name, df, note)
    combined = pd.concat([old, df], ignore_index=True)
    keys = [c for c in dedupe_on if c in combined.columns]
    if keys:
        combined = combined.drop_duplicates(keys, keep='last')
    added = len(combined) - len(old)
    return added, save(name, combined, note)


def load(name: str) -> pd.DataFrame | None:
    p = _path(name)
    if not os.path.exists(p):
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def read_meta() -> dict:
    try:
        with open(_meta_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def clear(name: str | None = None) -> None:
    for t in ([name] if name else TABLES):
        if os.path.exists(_path(t)):
            os.remove(_path(t))
    meta = read_meta()
    for t in ([name] if name else TABLES):
        meta.pop(t, None)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_meta_path(), 'w') as f:
        json.dump(meta, f, indent=2)
