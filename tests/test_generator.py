from __future__ import annotations

import filecmp
import json
from datetime import date
from pathlib import Path

import pandas as pd

from lakehouse_copilot.data_gen import generate
from lakehouse_copilot.data_gen import reference as ref
from lakehouse_copilot.data_gen.generator import festive_factor
from tests.conftest import SMALL


def _load(raw_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        "orders": pd.read_json(raw_dir / "orders/orders.jsonl", lines=True, dtype=False),
        "items": pd.read_csv(raw_dir / "order_items/order_items.csv"),
        "products": pd.read_json(raw_dir / "products/products.jsonl", lines=True, dtype=False),
        "customers": pd.read_csv(raw_dir / "customers/customers_2025-06-30.csv", dtype=str),
        "changes": pd.read_csv(raw_dir / "customers/customers_2025-07-01.csv", dtype=str),
    }


def _manifest(raw_dir: Path) -> dict:
    return json.loads((raw_dir / "_manifest.json").read_text())


def test_same_seed_gives_identical_files(tmp_path: Path) -> None:
    generate(tmp_path / "a", SMALL)
    generate(tmp_path / "b", SMALL)
    files = [p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.*")]
    match, mismatch, errors = filecmp.cmpfiles(tmp_path / "a", tmp_path / "b", files, shallow=False)
    assert not mismatch and not errors and len(match) == 6


def test_all_files_written(raw_dir: Path) -> None:
    for rel in _manifest(raw_dir)["raw_row_counts"]:
        assert (raw_dir / rel).exists(), rel


def test_dirty_counts_match_manifest(raw_dir: Path) -> None:
    d = _load(raw_dir)
    m = _manifest(raw_dir)["dirty_counts"]
    orders, items, products = d["orders"], d["items"], d["products"]
    unique_orders = orders.drop_duplicates()

    assert len(orders) - len(unique_orders) == m["orders_duplicate"]
    assert unique_orders["customer_id"].isna().sum() == m["orders_null_customer_id"]
    known_customers = set(d["customers"]["customer_id"].dropna()) | set(d["changes"]["customer_id"])
    ids = unique_orders["customer_id"]
    orphans = ids.notna() & ~ids.isin(known_customers)
    assert orphans.sum() == m["orders_orphan_customer_id"]

    unique_items = items.drop_duplicates()
    assert len(items) - len(unique_items) == m["items_duplicate"]
    assert (unique_items["quantity"] <= 0).sum() == m["items_non_positive_quantity"]
    known_products = set(products["product_id"])
    assert (~unique_items["product_id"].isin(known_products)).sum() == m["items_orphan_product_id"]

    assert len(products) - products["product_id"].nunique() == m["products_duplicate"]
    assert products.drop_duplicates("product_id")["brand"].isna().sum() == m["products_null_brand"]


def test_data_looks_indian(raw_dir: Path) -> None:
    d = _load(raw_dir)
    assert set(d["customers"]["city"]) <= {c[0] for c in ref.CITIES}
    assert d["customers"]["phone"].astype(str).str.startswith("+91").all()
    assert (d["products"]["unit_price"] > 0).all()
    variants = {v for vs in ref.PAYMENT_METHOD_VARIANTS.values() for v in vs}
    payments = set(d["orders"]["payment_method"].dropna())
    assert payments - variants == {"CRYPTO"}


def test_diwali_seasonality(raw_dir: Path) -> None:
    orders = _load(raw_dir)["orders"].drop_duplicates("order_id")
    ts = pd.to_datetime(orders["order_ts"], format="mixed", dayfirst=False, errors="coerce")
    # The legacy dd-MM-yyyy format needs its own parser.
    legacy = pd.to_datetime(orders["order_ts"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
    days = ts.where(~orders["order_ts"].str.match(r"\d{2}-\d{2}-\d{4}"), legacy).dt.date
    daily = days.value_counts()
    festive = daily[[festive_factor(day) > 1 for day in daily.index]].mean()
    normal = daily[[festive_factor(day) == 1 for day in daily.index]].mean()
    assert festive > 1.5 * normal


def test_festive_factor_peaks_on_diwali() -> None:
    assert festive_factor(date(2024, 11, 1)) > festive_factor(date(2024, 10, 20)) > 1
    assert festive_factor(date(2024, 6, 1)) == 1


def test_customer_change_batch(raw_dir: Path) -> None:
    d = _load(raw_dir)
    changes = _manifest(raw_dir)["customer_changes"]
    base = d["customers"].dropna(subset=["customer_id"]).drop_duplicates("customer_id")
    merged = d["changes"].merge(base, on="customer_id", how="left", suffixes=("", "_old"))
    new = merged["city_old"].isna()
    assert new.sum() == changes["new"]
    old = merged[~new]
    moved = (old["city"] != old["city_old"]).sum()
    upgraded = ((old["segment"] == "Premium") & (old["segment_old"] == "Regular")).sum()
    assert moved == changes["relocated"]
    assert upgraded == changes["upgraded"]
