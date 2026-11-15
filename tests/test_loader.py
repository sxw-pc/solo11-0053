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
    # 第 1 行为表头，数据行的记录号必须是源文件物理行号（2、3），而非数据行序号（1、2）
    assert batch.record_numbers == [2, 3]
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


def test_csv_record_numbers_are_physical_source_lines(tmp_path, schema):
    # 第 1 行表头；第 3 行为空行；记录号必须能直接定位回源文件物理行。
    path = tmp_path / "physical.csv"
    path.write_text(
        "id,amount,active,day,name\n"
        "1,12.5,true,2024-01-02,alpha\n"
        "\n"
        "2,3.5,false,2024-01-03,beta\n"
        "3,9.0,true,2024-01-04,gamma\n",
        encoding="utf-8",
    )
    batch = load(path, schema)
    assert [row["id"] for row in batch.rows] == [1, 2, 3]
    assert batch.record_numbers == [2, 4, 5]
    # 记录号与行数据一一对应：按物理行号回到源文件可找到该记录的 id
    lines = path.read_text(encoding="utf-8").splitlines()
    for record_no, row in zip(batch.record_numbers, batch.rows):
        assert lines[record_no - 1].startswith(f"{row['id']},")


def test_csv_record_numbers_handle_multiline_quoted_fields(tmp_path, schema):
    # 带引号的跨行字段只占一条记录，记录号取该记录的起始物理行。
    path = tmp_path / "multiline.csv"
    path.write_text(
        "id,name\n"
        "1,alpha\n"
        '2,"line-one\nline-two"\n'
        "3,gamma\n",
        encoding="utf-8",
    )
    batch = load(path, schema)
    assert [row["id"] for row in batch.rows] == [1, 2, 3]
    assert batch.record_numbers == [2, 3, 5]
    assert batch.rows[1]["name"] == "line-one\nline-two"


def test_jsonl_record_number_semantics_unchanged(tmp_path, schema):
    path = tmp_path / "lines.jsonl"
    path.write_text(
        '{"id": 1, "name": "a"}\n'
        "\n"
        "not-json\n"
        '{"id": 2, "name": "b"}\n',
        encoding="utf-8",
    )
    batch = load(path, schema)
    # 空行与非对象行被跳过后，记录号仍是源文件真实物理行号
    assert [row["id"] for row in batch.rows] == [1, 2]
    assert batch.record_numbers == [1, 4]
