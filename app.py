"""
Marketplace Dashboard — landing page.
Imports nothing heavy so this page always loads, even if a data page is broken.
"""
import streamlit as st
from auth import require_auth

try:
    BRAND = st.secrets.get('BRAND_NAME', 'Marketplace Dashboard')
except Exception:
    BRAND = 'Marketplace Dashboard'

st.set_page_config(page_title=BRAND, page_icon='🏠', layout='wide')
require_auth()

st.title(f'🏠 {BRAND}')
st.caption('Pick a page from the left sidebar.')

st.markdown("""
| Page | What it answers | Data comes from |
|---|---|---|
| 📊 **Sales** | How much did we sell, where, what is coming back, which styles are moving | Unicommerce orders |
| 📦 **Inventory** | What is in stock, how many days it will last, what is stuck | Unicommerce stock + Master sheet costs |
| 🛠 **Production** | What is being made, by whom, what is late | Production sheet |
| 💰 **Marketplace P&L** | What each marketplace actually leaves us after its deductions and our cost | Sales × Master sheet cost × channel rates |
| 📥 **Data Hub** | Load the Unicommerce export, Master sheet, Production sheet and stock | Excel / CSV upload |
""")

st.divider()
import data  # noqa: E402  (light import, only reads secrets)
src = data.source()
if src == 'demo':
    st.warning('Running on **demo data**. Load a real file on the 📥 Data Hub page to replace it.', icon='🧪')
elif src == 'local':
    st.info('Reading files uploaded through the Data Hub, stored in this folder. '
            'Connect Supabase to make them permanent and shared.', icon='💾')
else:
    st.success('Connected to Supabase.', icon='✅')
