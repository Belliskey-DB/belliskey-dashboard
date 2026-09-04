"""
auth.py — one shared-password gate for every page.

Streamlit serves each file in pages/ at its own URL, so a gate in app.py
protects nothing. Call require_auth() at the top of EVERY page.

The rule, in order:

  1. APP_PASSWORD is set            -> ask for it. Always, even on demo data.
  2. No password, but real data is  -> LOCK, with instructions. A deployed app
     loaded (a database, or files       is a public URL; real sales data behind
     uploaded through the Data Hub)     no password is the same as publishing it.
  3. No password, no real data      -> open. There is nothing to protect but
                                       generated sample numbers.

Rule 2 fails CLOSED on purpose. The common way dashboards leak is a missing
secret silently meaning "no gate" instead of "no entry".
"""
from __future__ import annotations

import hmac
import time
import streamlit as st


def _secret(key: str, default: str = '') -> str:
    try:
        return str(st.secrets.get(key, default) or default)
    except Exception:
        return default


def db_configured() -> bool:
    return bool(_secret('SUPABASE_HOST')) and bool(_secret('SUPABASE_PASSWORD'))


def has_real_data() -> bool:
    if db_configured():
        return True
    try:
        import store
        return store.has_any()
    except Exception:
        return False


def require_auth() -> None:
    password = _secret('APP_PASSWORD')

    if not password:
        if has_real_data():
            st.error(
                'This app holds real business data but no **APP_PASSWORD** is set, so it is '
                'refusing to open rather than showing the data to anyone with the link.',
                icon='🔒')
            st.markdown(
                'To fix it: **Streamlit Cloud → your app → ⋮ → Settings → Secrets**, add the line '
                'below, then click Save. The app restarts on its own.\n\n'
                '```toml\nAPP_PASSWORD = "pick-something-long"\n```\n\n'
                'Running on your own machine? Put the same line in `.streamlit/secrets.toml`.')
            st.stop()
        return  # demo data only — nothing worth protecting

    if st.session_state.get('auth_ok'):
        return

    fails = st.session_state.get('auth_fails', 0)
    if time.time() < st.session_state.get('auth_locked_until', 0):
        st.error('Too many wrong attempts. Try again in a few minutes.')
        st.stop()

    st.markdown('### 🔒 Sign in')
    entered = st.text_input('Password', type='password', key='auth_pw_input')
    if entered:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state['auth_ok'] = True
            st.session_state['auth_fails'] = 0
            st.rerun()
        else:
            fails += 1
            st.session_state['auth_fails'] = fails
            if fails >= 5:
                st.session_state['auth_locked_until'] = time.time() + 15 * 60
                st.session_state['auth_fails'] = 0
            st.error('Incorrect password.')
    st.stop()
