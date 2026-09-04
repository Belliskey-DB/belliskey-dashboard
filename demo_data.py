"""
demo_data.py — realistic sample data so the dashboard works before Supabase exists.

Everything is generated from a fixed seed, so it looks the same on every run.
Shapes match the tables in schema.sql exactly; once real data is loaded the
pages do not change.
"""
from __future__ import annotations

from datetime import date, timedelta
import numpy as np
import pandas as pd

SEED = 7
TODAY = date.today()

CHANNELS = pd.DataFrame([
    ('CH_MYNTRA',   'Myntra',   34.0, 40, 0.42, 0.25),
    ('CH_AMAZON',   'Amazon',   20.0, 40, 0.24, 0.15),
    ('CH_FLIPKART', 'Flipkart', 35.0, 40, 0.20, 0.28),
    ('CH_AJIO',     'Ajio',     47.0, 40, 0.10, 0.20),
    ('CH_NYKAA',    'Nykaa',    37.0, 40, 0.04, 0.22),
], columns=['channel_id', 'channel_name', 'deduction_pct', 'overhead_per_unit',
            '_share', '_return_rate'])

WAREHOUSES = pd.DataFrame([
    ('WH_MAIN', 'Main warehouse', 'MAIN'),
    ('WH_3PL',  '3PL Bhiwandi',   'BHIWANDI_3PL'),
], columns=['warehouse_id', 'warehouse_name', 'uc_facility_code'])

CATEGORIES = {
    'Kurta':        ('Women', 899,  260),
    'Kurta Set':    ('Women', 1499, 420),
    'Dress':        ('Women', 1299, 380),
    'Top':          ('Women', 699,  200),
    'Co-ord Set':   ('Women', 1699, 520),
    'Palazzo':      ('Women', 799,  210),
}
COLORS = ['Black', 'Navy', 'Maroon', 'Olive', 'Mustard', 'Teal', 'Pink', 'White']
SIZES  = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
STAGES = ['planning', 'fabric', 'cutting', 'stitching', 'finishing', 'received']
VENDORS = ['Aarav Apparels', 'Sunrise Garments', 'Noor Creations', 'Mehta Fabs']


def _rng():
    return np.random.default_rng(SEED)


def skus() -> pd.DataFrame:
    rng = _rng()
    rows = []
    style_no = 1000
    for cat, (gender, base_mrp, base_cost) in CATEGORIES.items():
        for _ in range(10):
            style_no += 1
            style = f'ST{style_no}'
            mrp = int(base_mrp * rng.uniform(0.85, 1.25) / 10) * 10 - 1
            cost = round(mrp * rng.uniform(0.20, 0.30), 0)
            launch = TODAY - timedelta(days=int(rng.integers(20, 540)))
            for color in rng.choice(COLORS, size=int(rng.integers(2, 4)), replace=False):
                for size in SIZES:
                    rows.append(dict(
                        sku_id=f'{style}-{color[:3].upper()}-{size}',
                        style_code=style,
                        product_name=f'{color} {cat}',
                        category=cat, gender=gender, color=color, size=size,
                        mrp=mrp, cost_price=cost, launch_date=launch, is_active=True,
                    ))
    return pd.DataFrame(rows)


def sales(days: int = 180) -> pd.DataFrame:
    rng = _rng()
    sku = skus()
    n_sku = len(sku)
    # Each style gets a popularity weight; a few are hits, a long tail is slow.
    style_w = pd.Series(rng.pareto(1.6, sku['style_code'].nunique()) + 0.2,
                        index=sku['style_code'].unique())
    size_w = pd.Series([0.6, 1.2, 1.5, 1.3, 0.9, 0.5], index=SIZES)
    w = (sku['style_code'].map(style_w) * sku['size'].map(size_w)).to_numpy()
    w = w / w.sum()
    ch = CHANNELS
    rows = []
    order_no = 500000
    for d in range(days, -1, -1):
        day = TODAY - timedelta(days=d)
        # weekly rhythm + slow growth + a sale spike ~40 days ago
        season = 1.0 + 0.25 * np.sin(2 * np.pi * (day.weekday()) / 7)
        growth = 1.0 + (days - d) / days * 0.6
        spike = 2.0 if 8 <= d <= 14 else 1.0   # a sale event last fortnight
        n_orders = int(rng.poisson(38 * season * growth * spike))
        for _ in range(n_orders):
            c = ch.iloc[rng.choice(len(ch), p=ch['_share'] / ch['_share'].sum())]
            i = rng.choice(n_sku, p=w)
            s = sku.iloc[i]
            qty = 1 if rng.random() < 0.9 else 2
            disc_pct = rng.choice([0.20, 0.30, 0.40, 0.50, 0.60], p=[0.15, 0.30, 0.30, 0.17, 0.08])
            gross = float(s['mrp']) * qty
            discount = round(gross * disc_pct, 2)
            net = round(gross - discount, 2)
            wh = 'WH_3PL' if c['channel_id'] in ('CH_AMAZON', 'CH_FLIPKART') and rng.random() < 0.7 else 'WH_MAIN'
            order_no += 1
            oid = f'ORD{order_no}'
            rows.append((day, c['channel_id'], wh, oid, s['sku_id'], qty, gross, discount, net, False))
            if rng.random() < c['_return_rate'] and d >= 3:
                rd = day + timedelta(days=int(rng.integers(5, 18)))
                if rd <= TODAY:
                    rows.append((rd, c['channel_id'], wh, oid, s['sku_id'], qty, gross, discount, net, True))
    df = pd.DataFrame(rows, columns=['sale_date', 'channel_id', 'warehouse_id', 'order_id', 'sku_id',
                                     'qty', 'gross_value', 'discount', 'net_value', 'return_flag'])
    df['sale_date'] = pd.to_datetime(df['sale_date'])
    return df


def stock() -> pd.DataFrame:
    rng = _rng()
    sku = skus()
    sold = sales(60).query('~return_flag').groupby('sku_id')['qty'].sum()
    rows = []
    for _, s in sku.iterrows():
        v = float(sold.get(s['sku_id'], 0)) / 60  # units per day
        # cover between 5 and 200 days, random; slow SKUs sit on big piles
        target_days = rng.uniform(5, 60) if v > 0.15 else rng.uniform(40, 220)
        total = int(max(0, v * target_days + rng.normal(0, 3)))
        if rng.random() < 0.08:
            total = 0  # stockouts
        main = int(total * rng.uniform(0.4, 0.8))
        rows.append((TODAY - timedelta(days=1), s['sku_id'], 'WH_MAIN', main))
        rows.append((TODAY - timedelta(days=2), s['sku_id'], 'WH_3PL', total - main))
    df = pd.DataFrame(rows, columns=['snapshot_date', 'sku_id', 'warehouse_id', 'stock_qty'])
    df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
    return df


def production() -> pd.DataFrame:
    rng = _rng()
    sku = skus()
    styles = sku.drop_duplicates('style_code')[['style_code', 'product_name', 'category', 'color']]
    rows = []
    for i in range(22):
        s = styles.iloc[int(rng.integers(0, len(styles)))]
        stage = STAGES[int(rng.choice(len(STAGES), p=[0.15, 0.2, 0.15, 0.25, 0.1, 0.15]))]
        planned = int(rng.choice([300, 400, 500, 600, 800, 1000]))
        received = planned if stage == 'received' else (int(planned * rng.uniform(0, 0.5)) if stage == 'finishing' else 0)
        po = TODAY - timedelta(days=int(rng.integers(5, 120)))
        rows.append(dict(
            lot_id=f'LOT-{240 + i}', style_code=s['style_code'], product_name=s['product_name'],
            category=s['category'], vendor=VENDORS[int(rng.integers(0, len(VENDORS)))],
            color=s['color'], planned_qty=planned, received_qty=received,
            po_date=po, expected_date=po + timedelta(days=int(rng.integers(35, 75))),
            current_stage=stage, updated_at=pd.Timestamp(TODAY),
        ))
    df = pd.DataFrame(rows)
    df['po_date'] = pd.to_datetime(df['po_date'])
    df['expected_date'] = pd.to_datetime(df['expected_date'])
    return df


def channels() -> pd.DataFrame:
    return CHANNELS[['channel_id', 'channel_name', 'deduction_pct', 'overhead_per_unit']].copy()


def warehouses() -> pd.DataFrame:
    return WAREHOUSES.copy()
