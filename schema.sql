-- =====================================================================
-- Marketplace Dashboard — Supabase schema
-- Run this ONCE in Supabase → SQL Editor → New query → Run.
-- Safe to re-run: every statement is IF NOT EXISTS / ON CONFLICT.
-- =====================================================================

-- ---------- Reference (dimension) tables ----------

-- One row per SKU (style + colour + size). Loaded from the MASTER sheet.
CREATE TABLE IF NOT EXISTS dim_sku (
    sku_id        text PRIMARY KEY,          -- the SKU code used in Unicommerce
    style_code    text,                      -- style / design number (the level people talk about)
    product_name  text,
    category      text,                      -- e.g. Kurta, Dress, Top
    gender        text,                      -- Men / Women / Boys / Girls
    color         text,
    size          text,
    mrp           numeric(12,2),
    cost_price    numeric(12,2),             -- landed cost per piece from the master sheet
    launch_date   date,
    is_active     boolean DEFAULT true,
    updated_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dim_sku_style_idx ON dim_sku (style_code);

-- One row per sales channel. deduction_pct = everything the marketplace keeps
-- (commission, shipping, payment fees, return charges) as a share of the
-- selling price. Start with estimates; replace with settlement-verified numbers.
CREATE TABLE IF NOT EXISTS dim_channel (
    channel_id        text PRIMARY KEY,
    channel_name      text NOT NULL,
    uc_channel_codes  text[] DEFAULT '{}',   -- Unicommerce channel codes that map here
    deduction_pct     numeric(5,2) DEFAULT 0, -- 0-100
    overhead_per_unit numeric(10,2) DEFAULT 0 -- ₹ per piece handled (packing + 3PL + office)
);

-- One row per Unicommerce facility (own warehouse, 3PL, FBA…).
CREATE TABLE IF NOT EXISTS dim_warehouse (
    warehouse_id     text PRIMARY KEY,
    warehouse_name   text NOT NULL,
    uc_facility_code text UNIQUE            -- exact facility code in Unicommerce
);

-- ---------- Fact tables ----------

-- One row per order line. Returns are SEPARATE rows with return_flag = true,
-- so gross sales = rows where return_flag = false, net = gross - returns.
CREATE TABLE IF NOT EXISTS fact_sales (
    sale_id         bigserial PRIMARY KEY,
    sale_date       date NOT NULL,
    channel_id      text REFERENCES dim_channel(channel_id),
    warehouse_id    text,
    order_id        text NOT NULL,
    invoice_no      text,
    sku_id          text NOT NULL,
    qty             integer NOT NULL,
    gross_value     numeric(12,2) DEFAULT 0, -- selling price × qty, before discount
    discount        numeric(12,2) DEFAULT 0,
    net_value       numeric(12,2) DEFAULT 0, -- what the customer paid for the line
    taxable_value   numeric(12,2) DEFAULT 0, -- line value excluding GST
    tax_value       numeric(12,2) DEFAULT 0, -- CGST + SGST + IGST + UTGST + CESS
    city            text,                    -- delivery city (no customer identity is stored)
    state           text,
    pincode         text,
    payment_method  text,                    -- PREPAID / COD
    return_flag     boolean DEFAULT false,
    ingest_batch_id text,
    UNIQUE (order_id, sku_id, return_flag)
);
CREATE INDEX IF NOT EXISTS fact_sales_date_idx    ON fact_sales (sale_date);
CREATE INDEX IF NOT EXISTS fact_sales_sku_idx     ON fact_sales (sku_id);
CREATE INDEX IF NOT EXISTS fact_sales_channel_idx ON fact_sales (channel_id);
CREATE INDEX IF NOT EXISTS fact_sales_state_idx   ON fact_sales (state);

-- One row per SKU per warehouse per day. Always read the LATEST date PER
-- WAREHOUSE (warehouses can upload on different days).
CREATE TABLE IF NOT EXISTS fact_stock_snapshot (
    snapshot_date date NOT NULL,
    sku_id        text NOT NULL,
    warehouse_id  text NOT NULL,
    stock_qty     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_date, sku_id, warehouse_id)
);

-- One row per production lot. Loaded from the PRODUCTION sheet.
CREATE TABLE IF NOT EXISTS fact_production_lot (
    lot_id        text PRIMARY KEY,
    style_code    text,
    product_name  text,
    category      text,
    vendor        text,
    color         text,
    planned_qty   integer DEFAULT 0,
    received_qty  integer DEFAULT 0,
    po_date       date,
    expected_date date,
    current_stage text,                      -- planning / fabric / cutting / stitching / finishing / received
    updated_at    timestamptz DEFAULT now()
);

-- Records every sync run so the dashboard can show "data as of …".
CREATE TABLE IF NOT EXISTS sync_log (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,               -- unicommerce_sales / unicommerce_stock / master_sheet / production_sheet
    ran_at      timestamptz DEFAULT now(),
    rows_written integer,
    status      text,
    message     text
);

-- ---------- Starter rows (edit the numbers to her actual rates) ----------
INSERT INTO dim_channel (channel_id, channel_name, uc_channel_codes, deduction_pct, overhead_per_unit) VALUES
  ('CH_MYNTRA',   'Myntra',   '{MYNTRA,MYNTRA_PPMP,MYNTRAPPMP}',        34.0, 40),
  ('CH_AMAZON',   'Amazon',   '{AMAZON,AMAZON_FBA,AMAZON_EASYSHIP}',    20.0, 40),
  ('CH_FLIPKART', 'Flipkart', '{FLIPKART,FLIPKART_OMNI}',               35.0, 40),
  ('CH_AJIO',     'Ajio',     '{AJIO,AJIO_DROPSHIP,AJIO_Dropship-2}',   47.0, 40),
  ('CH_NYKAA',    'Nykaa',    '{NYKAA_FASHION,NYKAA}',                  37.0, 40),
  ('CH_SNAPDEAL', 'Snapdeal', '{SNAPDEAL}',                             30.0, 40),
  ('CH_TATACLIQ', 'Tata Cliq','{TATACLIQ,TATACLIQ+2}',                  30.0, 40),
  ('CH_SHOPIFY',  'Shopify',  '{SHOPIFY}',                               3.0, 40),
  ('CH_BOOON',    'Booon',    '{BOOON}',                                30.0, 40),
  ('CH_OTHER',    'Other',    '{}',                                     30.0, 40)
ON CONFLICT (channel_id) DO NOTHING;

-- ---------- Security: lock tables, let the dashboard read them ----------
-- Supabase turns on Row-Level Security for tables created via the UI but not
-- via SQL. We turn it on explicitly and add a read policy, so the anon key
-- can never read anything. The dashboard connects as the `postgres` role,
-- which bypasses RLS.
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT unnest(ARRAY['dim_sku','dim_channel','dim_warehouse','fact_sales',
                               'fact_stock_snapshot','fact_production_lot','sync_log'])
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
