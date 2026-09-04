"""
sheets_sync.py — Google Sheets → Supabase (Master sheet and Production sheet). Run daily.

    python sheets_sync.py                 # both sheets
    python sheets_sync.py --only master
    python sheets_sync.py --only production

Config (env or .streamlit/secrets.toml):
    MASTER_SHEET_ID, MASTER_SHEET_TAB          (tab optional; first tab if blank)
    PRODUCTION_SHEET_ID, PRODUCTION_SHEET_TAB
    [gcp_service_account]  -> the service-account JSON (see README, "Google Sheets access")
    SUPABASE_*             -> as in db.py

The service account's email must be given VIEWER access on both sheets.
Column matching is in sheets.py.
"""
from __future__ import annotations

import argparse
import json
import logging
import os

import pandas as pd

import sheets
from uc_sync import cfg, db_conn, _SECRETS, log_run

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('sheets_sync')


def gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    info = _SECRETS.get('gcp_service_account') or json.loads(os.environ.get('GCP_SERVICE_ACCOUNT_JSON', '{}'))
    if not info:
        raise SystemExit('No Google service account configured — see README "Google Sheets access".')
    creds = Credentials.from_service_account_info(info, scopes=[
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'])
    return gspread.authorize(creds)


def read_sheet(gc, sheet_id: str, tab: str | None) -> pd.DataFrame:
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab) if tab else sh.get_worksheet(0)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    # header = first row that has at least 3 non-empty cells
    hdr = next((i for i, r in enumerate(values) if sum(bool(c.strip()) for c in r) >= 3), 0)
    return pd.DataFrame(values[hdr + 1:], columns=values[hdr]).replace('', None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['master', 'production'])
    a = ap.parse_args()
    gc = gspread_client()
    conn = db_conn()

    if a.only in (None, 'master'):
        sid = cfg('MASTER_SHEET_ID')
        if not sid:
            log.warning('MASTER_SHEET_ID not set — skipping master')
        else:
            raw = read_sheet(gc, sid, cfg('MASTER_SHEET_TAB'))
            clean, missing = sheets.clean_master(raw)
            if missing:
                log_run(conn, 'master_sheet', 0, 'error', f'missing columns {missing}; sheet has {list(raw.columns)}')
                raise SystemExit(f'Master sheet: cannot find {missing}. Headers seen: {list(raw.columns)}')
            n = sheets.write_master(conn, clean)
            log.info(f'master: {n} SKUs written')

    if a.only in (None, 'production'):
        sid = cfg('PRODUCTION_SHEET_ID')
        if not sid:
            log.warning('PRODUCTION_SHEET_ID not set — skipping production')
        else:
            raw = read_sheet(gc, sid, cfg('PRODUCTION_SHEET_TAB'))
            clean, missing = sheets.clean_production(raw)
            if missing:
                log_run(conn, 'production_sheet', 0, 'error', f'missing columns {missing}; sheet has {list(raw.columns)}')
                raise SystemExit(f'Production sheet: cannot find {missing}. Headers seen: {list(raw.columns)}')
            n = sheets.write_production(conn, clean)
            log.info(f'production: {n} lots written')
    conn.close()


if __name__ == '__main__':
    main()
