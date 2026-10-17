"""Generate synthetic Indian e-commerce source data with a fixed seed.

The output looks like what a real source system drops into a landing zone:

    data/raw/
      customers/customers_2025-06-30.csv   initial customer extract (batch 1)
      customers/customers_2025-07-01.csv   customer change batch (batch 2, drives SCD Type 2)
      products/products.jsonl              product master (JSON lines)
      orders/orders.jsonl                  order headers (JSON lines)
      order_items/order_items.csv          order lines (CSV)
      _manifest.json                       how many dirty records were injected, by type

Dirty records (nulls, duplicates, negative quantities, orphan keys, messy spellings,
two timestamp formats) are injected on purpose so the Silver layer has real work to do.
The manifest lets the tests check that every injected problem is caught.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from lakehouse_copilot.data_gen import reference as ref

EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com"]


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    n_customers: int = 2000
    n_products: int = 250
    start_date: date = date(2024, 1, 1)
    end_date: date = date(2025, 12, 31)
    base_daily_orders: float = 30.0
    # Customer extract dates: batch 1 is taken the day before the change date.
    change_date: date = date(2025, 7, 1)
    change_rate: float = 0.06
    noop_rate: float = 0.02
    dirty: bool = True
    dirty_rates: dict[str, float] = field(
        default_factory=lambda: {
            "orders_null_customer_id": 0.002,
            "orders_orphan_customer_id": 0.0015,
            "orders_invalid_payment_method": 0.001,
            "orders_duplicate": 0.005,
            "items_non_positive_quantity": 0.002,
            "items_orphan_product_id": 0.001,
            "items_duplicate": 0.004,
            "customers_duplicate": 0.005,
            "customers_null_customer_id": 0.0015,
            "customers_invalid_email": 0.004,
            "products_duplicate": 0.015,
            "products_null_brand": 0.01,
        }
    )


def _count(rate: float, population: int) -> int:
    """Number of records to corrupt: at least one, so small test datasets still get every defect."""
    return max(1, int(round(rate * population)))


def _choice(rng: np.random.Generator, weights: dict[str, float]) -> str:
    keys = list(weights)
    probs = np.array([weights[k] for k in keys], dtype=float)
    return keys[rng.choice(len(keys), p=probs / probs.sum())]


def festive_factor(day: date) -> float:
    """Demand multiplier: ramps up in the three weeks before Diwali and peaks on Diwali."""
    diwali = ref.DIWALI_DATES.get(day.year)
    if diwali is None:
        return 1.0
    delta = (diwali - day).days
    if 0 <= delta <= ref.FESTIVE_DAYS_BEFORE:
        return 1.0 + 1.6 * (1 - delta / ref.FESTIVE_DAYS_BEFORE) + 0.3
    if -ref.FESTIVE_DAYS_AFTER <= delta < 0:
        return 1.5
    return 1.0


def is_festive(day: date) -> bool:
    return festive_factor(day) > 1.0


# ---------------------------------------------------------------- customers


def _make_customers(cfg: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    city_weights = np.array([ref.CITY_TIER_WEIGHT[c[3]] for c in ref.CITIES])
    city_weights = city_weights / city_weights.sum()
    signup_start = date(2023, 1, 1)
    signup_span = (cfg.end_date - timedelta(days=60) - signup_start).days

    rows = []
    for i in range(1, cfg.n_customers + 1):
        first = ref.FIRST_NAMES[rng.integers(len(ref.FIRST_NAMES))]
        last = ref.LAST_NAMES[rng.integers(len(ref.LAST_NAMES))]
        city, state, region, tier = ref.CITIES[rng.choice(len(ref.CITIES), p=city_weights)]
        signup = signup_start + timedelta(days=int(rng.integers(signup_span)))
        local_part = f"{first}.{last}{rng.integers(1, 999)}".lower().replace("'", "")
        rows.append(
            {
                "customer_id": f"C{i:05d}",
                "first_name": first,
                "last_name": last,
                "email": f"{local_part}@{EMAIL_DOMAINS[rng.integers(len(EMAIL_DOMAINS))]}",
                "phone": f"+91{rng.integers(6, 10)}{rng.integers(0, 10**9):09d}",
                "city": city,
                "state": state,
                "region": region,
                "city_tier": tier,
                "segment": "Premium" if rng.random() < 0.2 else "Regular",
                "signup_date": signup.isoformat(),
                "updated_at": signup.isoformat(),
            }
        )
    return pd.DataFrame(rows)


def _make_customer_changes(
    cfg: GeneratorConfig, customers: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build the change batch: relocations and upgrades (Type 2), email changes (Type 1),
    unchanged re-sends (no-ops) and customers who signed up after the first extract."""
    cutoff = cfg.change_date.isoformat()
    existing = customers[customers["signup_date"] < cutoff]
    new = customers[customers["signup_date"] >= cutoff].copy()

    n_changed = _count(cfg.change_rate, len(existing))
    n_noop = _count(cfg.noop_rate, len(existing))
    picked = rng.choice(existing.index.to_numpy(), size=n_changed + n_noop, replace=False)
    changed_idx, noop_idx = picked[:n_changed], picked[n_changed:]

    changed = customers.loc[changed_idx].copy()
    counts = {"relocated": 0, "upgraded": 0, "email_changed": 0}
    for idx in changed.index:
        roll = rng.random()
        if roll < 0.10:
            changed.at[idx, "email"] = changed.at[idx, "email"].replace("@", ".new@")
            counts["email_changed"] += 1
        elif roll < 0.45 and changed.at[idx, "segment"] == "Regular":
            changed.at[idx, "segment"] = "Premium"
            counts["upgraded"] += 1
        else:
            current_city = changed.at[idx, "city"]
            options = [c for c in ref.CITIES if c[0] != current_city]
            city, state, region, tier = options[rng.integers(len(options))]
            changed.loc[idx, ["city", "state", "region", "city_tier"]] = [city, state, region, tier]
            counts["relocated"] += 1
    changed["updated_at"] = cutoff

    noop = customers.loc[noop_idx].copy()
    noop["updated_at"] = cutoff

    batch = pd.concat([changed, noop, new]).sort_values("customer_id").reset_index(drop=True)
    counts |= {
        "noop": len(noop),
        "new": len(new),
        "type2_changes": counts["relocated"] + counts["upgraded"],
    }
    return batch, counts


# ---------------------------------------------------------------- products


def _make_products(cfg: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for i in range(1, cfg.n_products + 1):
        category = _choice(rng, ref.CATEGORY_WEIGHT)
        spec = ref.CATEGORIES[category]
        sub = spec["sub_categories"][rng.integers(len(spec["sub_categories"]))]
        brand = spec["brands"][rng.integers(len(spec["brands"]))]
        lo, hi = spec["price"]
        # Log-uniform price, rounded to an Indian-style price point like 1,499.
        price = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
        price = max(lo, round(price / 50) * 50 - 1)
        margin = rng.uniform(*spec["margin"])
        rows.append(
            {
                "product_id": f"P{i:04d}",
                "product_name": f"{brand} {sub} {rng.integers(100, 999)}",
                "category": category,
                "sub_category": sub,
                "brand": brand,
                "unit_price": float(price),
                "cost_price": round(price * (1 - margin), 2),
                "updated_at": "2023-12-31",
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- orders


def _format_ts(ts: datetime, rng: np.random.Generator) -> str:
    """Most orders use ISO format; ~10% come from a legacy system with dd-MM-yyyy."""
    if rng.random() < 0.10:
        return ts.strftime("%d-%m-%Y %H:%M:%S")
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def _make_orders(
    cfg: GeneratorConfig,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    signup = pd.to_datetime(customers["signup_date"]).dt.date.to_numpy()
    cust_ids = customers["customer_id"].to_numpy()
    tiers = dict(zip(customers["customer_id"], customers["city_tier"], strict=True))
    # A few customers buy a lot, most buy a little (Pareto-like activity).
    activity = rng.pareto(2.0, size=len(customers)) + 0.2

    products_by_cat = {c: g for c, g in products.groupby("category")}
    total_days = (cfg.end_date - cfg.start_date).days + 1

    orders, items = [], []
    order_seq, item_seq = 0, 0
    for offset in range(total_days):
        day = cfg.start_date + timedelta(days=offset)
        weekend = 1.2 if day.weekday() >= 5 else 1.0
        growth = 1.0 + 0.25 * offset / 365
        festive = festive_factor(day)
        n_orders = rng.poisson(cfg.base_daily_orders * weekend * growth * festive)

        eligible = np.flatnonzero(signup <= day)
        if n_orders == 0 or len(eligible) == 0:
            continue
        weights = activity[eligible] / activity[eligible].sum()
        buyers = cust_ids[rng.choice(eligible, size=n_orders, p=weights)]

        cat_weights = {
            c: w * (ref.FESTIVE_CATEGORY_BOOST.get(c, 1.0) if festive > 1 else 1.0)
            for c, w in ref.CATEGORY_WEIGHT.items()
        }

        for customer_id in buyers:
            order_seq += 1
            order_id = f"O{order_seq:07d}"
            ts = datetime(day.year, day.month, day.day) + timedelta(
                seconds=int(rng.integers(6 * 3600, 24 * 3600))
            )

            pay_weights = dict(ref.PAYMENT_METHODS)
            if tiers[customer_id] == 3:
                pay_weights["COD"] *= 2.0
            payment = _choice(rng, pay_weights)
            variants = ref.PAYMENT_METHOD_VARIANTS[payment]
            raw_payment = variants[rng.integers(len(variants))]

            if (cfg.end_date - day).days < 10:
                status = _choice(rng, {"Shipped": 0.7, "Delivered": 0.2, "Cancelled": 0.1})
            else:
                status_weights = {k: v for k, v in ref.ORDER_STATUSES.items() if k != "Shipped"}
                if payment == "COD":
                    status_weights["Cancelled"] *= 1.8
                status = _choice(rng, status_weights)

            orders.append(
                {
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "order_ts": _format_ts(ts, rng),
                    "payment_method": raw_payment,
                    "order_status": status,
                    "channel": _choice(rng, ref.CHANNELS),
                }
            )

            for _ in range(min(5, 1 + rng.poisson(0.6))):
                item_seq += 1
                category = _choice(rng, cat_weights)
                pool = products_by_cat[category]
                product = pool.iloc[rng.integers(len(pool))]
                max_qty = 5 if category == "Grocery" else 3
                qty = int(min(max_qty, rng.choice([1, 1, 1, 1, 1, 1, 1, 2, 2, 3])))
                if festive > 1:
                    discount = float(rng.choice([10, 15, 20, 25, 30, 40]))
                else:
                    discount = float(rng.choice([0, 0, 5, 10]))
                items.append(
                    {
                        "order_item_id": f"I{item_seq:08d}",
                        "order_id": order_id,
                        "product_id": product["product_id"],
                        "quantity": qty,
                        "unit_price": product["unit_price"],
                        "discount_pct": discount,
                    }
                )

    return pd.DataFrame(orders), pd.DataFrame(items)


# ---------------------------------------------------------------- dirty data


def _inject_dirty(
    cfg: GeneratorConfig,
    customers_b1: pd.DataFrame,
    products: pd.DataFrame,
    orders: pd.DataFrame,
    items: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    r = cfg.dirty_rates
    m: dict[str, int] = {}
    orders, items = orders.copy(), items.copy()
    customers_b1, products = customers_b1.copy(), products.copy()

    # Orders: pick disjoint rows for each defect so the expected counts are exact.
    n_null = _count(r["orders_null_customer_id"], len(orders))
    n_orphan = _count(r["orders_orphan_customer_id"], len(orders))
    n_pay = _count(r["orders_invalid_payment_method"], len(orders))
    bad = rng.choice(len(orders), size=n_null + n_orphan + n_pay, replace=False)
    null_idx, orphan_idx, pay_idx = np.split(bad, [n_null, n_null + n_orphan])
    orders.loc[null_idx, "customer_id"] = None
    orders.loc[orphan_idx, "customer_id"] = [f"C9{rng.integers(0, 9999):04d}" for _ in orphan_idx]
    orders.loc[pay_idx, "payment_method"] = [
        None if i % 2 == 0 else "CRYPTO" for i in range(len(pay_idx))
    ]
    m |= {
        "orders_null_customer_id": n_null,
        "orders_orphan_customer_id": n_orphan,
        "orders_invalid_payment_method": n_pay,
    }
    bad_order_ids = set(orders.loc[bad, "order_id"])

    # Order items whose parent order is bad get quarantined too (referential cascade).
    m["items_of_bad_orders"] = int(items["order_id"].isin(bad_order_ids).sum())

    # Item defects only on lines of clean orders, again disjoint.
    clean_items = np.flatnonzero(~items["order_id"].isin(bad_order_ids).to_numpy())
    n_qty = _count(r["items_non_positive_quantity"], len(items))
    n_prod = _count(r["items_orphan_product_id"], len(items))
    bad_items = rng.choice(clean_items, size=n_qty + n_prod, replace=False)
    qty_idx, prod_idx = bad_items[:n_qty], bad_items[n_qty:]
    items.loc[qty_idx, "quantity"] = rng.choice([-3, -2, -1, 0], size=n_qty)
    items.loc[prod_idx, "product_id"] = [f"P9{rng.integers(0, 999):03d}" for _ in prod_idx]
    m |= {"items_non_positive_quantity": n_qty, "items_orphan_product_id": n_prod}

    # Exact duplicate rows (the source re-sent them). Only duplicate clean rows.
    clean_orders = np.setdiff1d(np.arange(len(orders)), bad)
    n_dup = _count(r["orders_duplicate"], len(orders))
    orders = pd.concat([orders, orders.iloc[rng.choice(clean_orders, n_dup, replace=False)]])
    m["orders_duplicate"] = n_dup

    clean_items = np.setdiff1d(clean_items, bad_items)
    n_dup = _count(r["items_duplicate"], len(items))
    items = pd.concat([items, items.iloc[rng.choice(clean_items, n_dup, replace=False)]])
    m["items_duplicate"] = n_dup

    # Customers: duplicates, junk rows with no id, and malformed emails (a warning, not an error).
    n_email = _count(r["customers_invalid_email"], len(customers_b1))
    email_idx = rng.choice(len(customers_b1), n_email, replace=False)
    customers_b1.loc[email_idx, "email"] = customers_b1.loc[email_idx, "email"].str.replace(
        "@", "_at_"
    )
    m["customers_invalid_email"] = n_email

    n_dup = _count(r["customers_duplicate"], len(customers_b1))
    dups = customers_b1.iloc[rng.choice(len(customers_b1), n_dup, replace=False)]
    n_null = _count(r["customers_null_customer_id"], len(customers_b1))
    junk = customers_b1.iloc[rng.choice(len(customers_b1), n_null, replace=False)].copy()
    junk["customer_id"] = None
    customers_b1 = pd.concat([customers_b1, dups, junk])
    m |= {"customers_duplicate": n_dup, "customers_null_customer_id": n_null}

    # Products: a missing brand (filled with 'Unknown' in Silver) and re-sent rows.
    n_brand = _count(r["products_null_brand"], len(products))
    products.loc[rng.choice(len(products), n_brand, replace=False), "brand"] = None
    n_dup = _count(r["products_duplicate"], len(products))
    products = pd.concat([products, products.iloc[rng.choice(len(products), n_dup, replace=False)]])
    m |= {"products_null_brand": n_brand, "products_duplicate": n_dup}

    # Shuffle so duplicates are not neatly at the end of the file.
    orders = orders.sample(frac=1, random_state=cfg.seed).reset_index(drop=True)
    items = items.sample(frac=1, random_state=cfg.seed).reset_index(drop=True)
    return customers_b1, products, orders, items, m


# ---------------------------------------------------------------- entry point


def generate(out_dir: str | Path, cfg: GeneratorConfig | None = None) -> dict:
    """Generate all source files under `out_dir` and return the manifest."""
    cfg = cfg or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)
    out = Path(out_dir)

    customers = _make_customers(cfg, rng)
    products = _make_products(cfg, rng)
    orders, items = _make_orders(cfg, customers, products, rng)

    batch1 = customers[customers["signup_date"] < cfg.change_date.isoformat()]
    batch2, change_counts = _make_customer_changes(cfg, customers, rng)

    clean_counts = {
        "customers": len(customers),
        "customers_batch1": len(batch1),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
    }
    dirty_counts: dict[str, int] = {}
    if cfg.dirty:
        batch1, products, orders, items, dirty_counts = _inject_dirty(
            cfg, batch1, products, orders, items, rng
        )

    batch1_date = (cfg.change_date - timedelta(days=1)).isoformat()
    files = {
        f"customers/customers_{batch1_date}.csv": batch1,
        f"customers/customers_{cfg.change_date.isoformat()}.csv": batch2,
        "products/products.jsonl": products,
        "orders/orders.jsonl": orders,
        "order_items/order_items.csv": items,
    }
    for rel, df in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".csv"):
            df.to_csv(path, index=False)
        else:
            df.to_json(path, orient="records", lines=True, force_ascii=False)

    manifest = {
        "config": {k: str(v) if isinstance(v, date) else v for k, v in asdict(cfg).items()},
        "clean_counts": clean_counts,
        "customer_changes": change_counts,
        "dirty_counts": dirty_counts,
        "raw_row_counts": {rel: len(df) for rel, df in files.items()},
    }
    (out / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
