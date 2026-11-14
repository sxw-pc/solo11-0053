#!/usr/bin/env python3
"""Generate the repository's synthetic, deterministic demo batches.

All people, identifiers, and values are fabricated. No network access.
"""

from __future__ import annotations

import csv
import io
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCHES = ROOT / "fixtures" / "batches"
BASELINE = BATCHES / "baseline"
COLUMNS = ["order_id", "customer_id", "region", "amount", "status", "created_at"]
REGIONS = ["north", "south", "east", "west"]
STATUSES = ["paid", "pending", "refunded", "cancelled"]
START = date(2024, 1, 2)


def deterministic_amount(index: int) -> float:
    return round(12 + ((index * 173) % 4600) + (index % 4) * 0.25, 2)


def make_row(prefix: str, index: int, region: str, customer: str | None = None) -> dict:
    return {
        "order_id": f"ord-{prefix}-{index:04d}",
        "customer_id": customer or f"cust-{prefix}-{index:04d}",
        "region": region,
        "amount": deterministic_amount(index),
        "status": STATUSES[index % len(STATUSES)],
        "created_at": (START + timedelta(days=index)).isoformat(),
    }


def baseline_rows() -> list[dict]:
    rng = random.Random(4101)
    rows = []
    for index in range(40):
        region = REGIONS[index % len(REGIONS)]
        row = make_row("base", index + 1, region, f"cust-base-{index:03d}")
        row["amount"] = round(20 + rng.random() * 4200, 2)
        row["status"] = STATUSES[(index * 3 + 1) % len(STATUSES)]
        rows.append(row)
    return rows


def batch_a_rows() -> list[dict]:
    rows = []
    for index in range(24):
        region = REGIONS[index % len(REGIONS)]
        row = make_row("a", index + 1, region)
        if index in {5, 6}:
            row["customer_id"] = "cust-a-repeat"
        rows.append(row)
    return rows


def batch_b_rows() -> list[dict]:
    regions = ["east"] * 12 + ["west"] * 6 + ["north"] * 2
    rows = []
    for index, region in enumerate(regions):
        row = make_row("b", index + 1, region)
        if index == 2:
            row["region"] = None
        if index == 9:
            row["customer_id"] = "cust-b-repeat"
        if index == 10:
            row["customer_id"] = "cust-b-repeat"
        rows.append(row)
    return rows


def batch_bad_rows() -> list[dict]:
    rows = []
    for index in range(24):
        region = REGIONS[(index + 2) % len(REGIONS)]
        customer = f"cust-bad-{index % 6:03d}"
        row = make_row("bad", index + 1, region, customer)
        rows.append(row)
    rows[0]["order_id"] = "ord-bad-duplicate"
    rows[1]["order_id"] = "ord-bad-duplicate"
    rows[3]["status"] = "void"
    rows[4]["amount"] = -5.0
    rows[5]["amount"] = 99999.0
    for index in (0, 1, 2):
        rows[index]["region"] = None
    return rows


def batch_cross_rows() -> list[dict]:
    """A small batch with an uneven region×status cross and sparse strata.

    Sixteen rows across nine non-empty (region, status) combinations, two of
    which hold a single row each, plus one row whose fields are both null.
    """
    cells = [
        ("north", "paid", 4),
        ("north", "refunded", 3),
        ("south", "paid", 2),
        ("south", "pending", 1),
        ("east", "cancelled", 1),
        ("east", "refunded", 1),
        ("west", "paid", 2),
        ("west", "pending", 1),
        ("north", "cancelled", 1),
    ]
    rows = []
    for region, status, count in cells:
        for _ in range(count):
            index = len(rows) + 1
            row = make_row("cross", index, region)
            row["status"] = status
            rows.append(row)
    row = make_row("cross", len(rows) + 1, None)
    row["status"] = None
    rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(buffer.getvalue().encode("utf-8"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def main() -> None:
    BASELINE.mkdir(parents=True, exist_ok=True)
    write_csv(BASELINE / "batch_base.csv", baseline_rows())
    write_csv(BATCHES / "batch_a.csv", batch_a_rows())
    write_jsonl(BATCHES / "batch_b.jsonl", batch_b_rows())
    write_csv(BATCHES / "batch_bad.csv", batch_bad_rows())
    write_csv(BATCHES / "batch_cross.csv", batch_cross_rows())
    for path in (
        BATCHES / "batch_a.csv",
        BATCHES / "batch_b.jsonl",
        BATCHES / "batch_bad.csv",
        BATCHES / "batch_cross.csv",
        BASELINE / "batch_base.csv",
    ):
        print(f"wrote {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
