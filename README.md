# Belliskey — Marketplace Dashboard

A Streamlit dashboard for a clothing brand that sells only on marketplaces
(Myntra, Flipkart, Nykaa, Amazon, Ajio), runs its backend on **Unicommerce**,
and keeps costs in a **Master** Google Sheet and open orders in a **Production**
Google Sheet.

## Start here: the Data Hub

You do **not** need Unicommerce API access or Supabase to see real numbers.
Export **Unicommerce → Reports → Tally GST Report** for any date range, open the
📥 Data Hub page, drop the file in, and every page fills in. Uploads are saved
to a `data/` folder beside the app until Supabase is connected.

Loaded on 4 Sep 2026: Jun–Aug 2026, 7,140 invoice lines, ₹59.9L, 7,155 units,
785 barcodes, across Myntra, Flipkart, Nykaa, Amazon, Ajio and a few small ones.

Two things the sales export does not contain, so they wait for the Master sheet:
**cost price** (no profit without it) and **style code and size** (each row is
one barcode until then).

## Pages

| Page | Answers | Source |
|---|---|---|
| 📊 Sales | Net sales (after returns), units, orders, ASP, daily trend, channel mix, category split, top styles, size curve | Unicommerce orders |
| 📦 Inventory | Units and value on hand, days of cover per style, low-cover list, dead stock, styles selling with zero stock, size × warehouse drill | Unicommerce stock + Master sheet cost |
| 🛠 Production | Open lots, units to receive, overdue lots, by stage / vendor, "making but not selling" and "selling but not making" | Production sheet |
| 💰 Marketplace P&L | Contribution per channel after marketplace deductions, cost of goods, and handling; category × channel heatmap; loss-making styles | Sales × Master cost × channel rates |
| 📥 Upload | Load Master / Production / stock export by hand | Excel or CSV |

## Run it locally (demo data)

```bash
cd marketplace-dashboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Open http://localhost:8501.

## Connect real data — three steps

### 1. Supabase (15 minutes)

1. https://supabase.com → New project. Region: **Mumbai (ap-south-1)**. Save the database password.
2. SQL Editor → New query → paste all of `schema.sql` → Run. This creates the tables and starter channel rows.
3. Project → Connect → **Session pooler** tab. Copy host, port, user.
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, fill in `SUPABASE_*`, `APP_PASSWORD`, `BRAND_NAME`.
5. Still in SQL Editor, tell the app which Unicommerce facilities exist:

```sql
INSERT INTO dim_warehouse (warehouse_id, warehouse_name, uc_facility_code) VALUES
  ('WH_MAIN', 'Main warehouse', 'EXACT_FACILITY_CODE_FROM_UNICOMMERCE'),
  ('WH_3PL',  '3PL',            'ANOTHER_FACILITY_CODE');
```

Facility codes are in Unicommerce → Settings → Facilities. Channel codes are in
Unicommerce → Settings → Channels; put them in `dim_channel.uc_channel_codes`
if they differ from the defaults in `schema.sql`.

Restart the app. The demo banner disappears and the password prompt appears.

### 2. Master and Production sheets

Quickest: 📥 Upload page → drop the Excel export of each sheet. The page shows
which columns it recognised before writing anything. If a required column is
not found, either rename the header in the sheet or add her header name to the
lists at the top of `sheets.py`.

Automatic (daily): create a Google service account (Cloud Console → IAM →
Service Accounts → Create → Keys → JSON), share both sheets with its
`client_email` as Viewer, paste the JSON into `secrets.toml` under
`[gcp_service_account]`, set `MASTER_SHEET_ID` / `PRODUCTION_SHEET_ID`, then:

```bash
.venv/bin/python sheets_sync.py
```

### 3. Unicommerce

**The easy way, working today:** export the Tally GST Report from Unicommerce
and upload it on the Data Hub page. Do the same for the Tally *Return* GST
Report so returns are counted. No API access needed. Re-uploading an overlapping
period is safe — rows are matched on invoice and barcode.

**The automatic way (needs an API user):**

Ask Unicommerce support to enable API access and create an API user. Put
`UC_TENANT` (the part before `.unicommerce.com`), `UC_USERNAME`, `UC_PASSWORD`
in `secrets.toml`, then:

```bash
.venv/bin/python uc_sync.py --days 90     # first time: backfill 90 days
.venv/bin/python uc_sync.py               # daily: last 2 days + full stock
```

Load the Master sheet **before** the first stock sync: the stock call needs the
SKU list.

## Daily automation

Run both sync scripts once a day. Simplest is a GitHub Actions cron (secrets as
repository secrets) or a `launchd` job on a Mac that is always on. Each run
writes a row to `sync_log`; the pages show "Data as of …" from it.

## Deploy to Streamlit Cloud

1. **share.streamlit.io → Create app → Deploy a public app from GitHub.**
   Repository `alinshah-hue/belliskey-dashboard`, branch `main`, main file `app.py`.
   Pick any URL you like.
2. **Before opening it, set the password.** ⋮ → Settings → Secrets, paste:

   ```toml
   APP_PASSWORD = "pick-something-long"
   BRAND_NAME   = "Belliskey"
   ```

   Save. The app restarts by itself. Without this line the app refuses to open
   once real data is loaded, which is deliberate — a Streamlit URL is public.
3. **Open the app, go to 📥 Data Hub, upload the Tally GST Report.** The Sales
   page fills in immediately.

Uploads live in temporary storage on the Streamlit server. They survive while
the app is awake and are cleared when it restarts or sleeps, at which point you
upload again. Adding the Supabase details to the same Secrets box is what makes
data permanent and shared between people.

## How the numbers are defined

- **Net sales** = value of units shipped (after discount) − value of units returned in the period. Every headline uses net.
- **Returns** are separate rows with `return_flag = true`; the return is dated when it was recorded, not when the order was placed.
- **Stock** is always the latest snapshot **per warehouse**; warehouses can be on different dates and the page says so.
- **Days of cover** = stock ÷ average daily units over the last 30 days.
- **Contribution** = net sales − deductions (rate × gross) − cost of goods (cost × units kept) − handling (₹/piece × forward and return pieces). It is an estimate until the deduction rates are replaced with settlement-verified numbers.

## Files

```
app.py            landing page
auth.py           shared-password gate (fails closed once a DB is configured)
db.py             Supabase connection
data.py           the only place pages get data from (Supabase or demo)
demo_data.py      generated sample data
sheets.py         column matching for the Master / Production sheets
ui.py             page setup, filters, chart helpers
fmt.py            ₹ formatting (lakh / crore)
uc_sync.py        Unicommerce → Supabase (orders + stock)
sheets_sync.py    Google Sheets → Supabase
schema.sql        run once in Supabase
pages/            the five pages
```
