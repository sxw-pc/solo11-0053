from __future__ import annotations

import hashlib

import pytest

from gatesampler.config import ColumnSpec, SchemaSpec
from gatesampler.errors import GateSamplerError
from gatesampler.loader import load_batch


@pytest.fixture
def schema() -> SchemaSpec:
    return SchemaSpec(
        {
            "id": ColumnSpec("id", "int", True),
            "amount": ColumnSpec("amount", "float"),
            "active": ColumnSpec("active", "bool"),
            "day": ColumnSpec("day", "date"),
            "name": ColumnSpec("name", "str"),
        }
    )


def load(path, schema, **kwargs):
    return load_batch(path, batch_id="test_batch", schema=schema, **kwargs)


def test_csv_utf8_bom_types_nulls_and_hash(tmp_path, schema):
    raw = (
        "﻿id,amount,active,day,name\n"
        "1,12.50,true,2024-01-02,alpha\n"
        "2,,0,2024-01-03,NULL\n"
    ).encode()
    path = tmp_path / "batch.csv"
    path.write_bytes(raw)
    batch = load(path, schema)
    assert batch.row_count == 2
    assert batch.column_count == 5
    assert batch.rows[0] == {
        "id": 1,
        "amount": 12.5,
        "active": True,
        "day": "2024-01-02",
        "name": "alpha",
    }
    assert batch.rows[1]["amount"] is None
    assert batch.rows[1]["active"] is False
    assert batch.rows[1]["name"] is None
    assert batch.record_numbers == [1, 2]
    assert batch.source_sha256 == hashlib.sha256(raw).hexdigest()


def test_csv_extra_and_missing_columns_warn_and_strict(tmp_path, schema):
    path = tmp_path / "warn.csv"
    path.write_text("id,extra\n1,x\n", encoding="utf-8")
    batch = load(path, schema)
    codes = {warning.code for warning in batch.warnings}
    assert {"GS-LOAD-101", "GS-LOAD-102"}.issubset(codes)
    with pytest.raises(GateSamplerError) as caught:
        load(path, schema, strict=True)
    assert caught.value.code in {"GS-LOAD-101", "GS-LOAD-102"}


def test_csv_allow_extra_columns(tmp_path, schema):
    allow_schema = SchemaSpec(
        dict(schema.columns), allow_extra_columns=True, null_tokens=schema.null_tokens
    )
    path = tmp_path / "extra.csv"
    path.write_text("id,extra\n1,x\n", encoding="utf-8")
    batch = load(path, allow_schema)
    assert "GS-LOAD-102" not in {warning.code for warning in batch.warnings}
    assert batch.rows[0]["extra"] == "x"


def test_jsonl_typed_rows_and_bad_lines(tmp_path, schema):
    path = tmp_path / "batch.jsonl"
    path.write_text(
        '{"id": 1, "amount": 2, "active": "yes", "day": "2024-02-01", "name": "a"}\n'
        '{"id": 2, "amount": 3.5, "active": false, "day": "2024-02-02"}\n'
        "\n"
        "not-json\n"
        "[1, 2]\n",
        encoding="utf-8",
    )
    batch = load(path, schema)
    assert batch.row_count == 2
    assert batch.rows[0]["active"] is True
    assert batch.rows[1]["name"] is None
    assert batch.record_numbers == [1, 2]
    codes = [warning.code for warning in batch.warnings]
    assert "GS-LOAD-004" in codes
    assert "GS-LOAD-005" in codes


def test_type_conversion_warning(tmp_path, schema):
    path = tmp_path / "types.csv"
    path.write_text("id,amount,active,day,name\nnope,bad,maybe,not-a-date,ok\n", encoding="utf-8")
    batch = load(path, schema)
    conversion = [warning for warning in batch.warnings if warning.code == "GS-LOAD-103"]
    assert len(conversion) == 4
    assert batch.rows[0]["id"] == "nope"


def test_empty_and_unsupported_and_missing(tmp_path, schema):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    batch = load(empty, schema)
    assert batch.row_count == 0
    assert batch.warnings[0].code == "GS-LOAD-104"
    with pytest.raises(GateSamplerError) as caught:
        load(tmp_path / "missing.csv", schema)
    assert caught.value.code == "GS-LOAD-001"
    bad = tmp_path / "data.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(GateSamplerError) as caught:
        load(bad, schema)
    assert caught.value.code == "GS-LOAD-002"


def test_invalid_utf8(tmp_path, schema):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"id\n\xff\n")
    with pytest.raises(GateSamplerError) as caught:
        load(path, schema)
    assert caught.value.code == "GS-LOAD-002"
