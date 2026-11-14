"""Validation helpers for contracts and batch declarations.

Assertion parameter validation is owned by the guards themselves: each guard
declares its ``param_schema`` and this module only dispatches through the
single ordered registry in :mod:`gatesampler.assertions`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .assertions import GUARD_TYPES
from .assertions import validate_assertion_params as _validate_assertion_params
from .assertions.base import (  # re-exported for the public config API
    boolean,
    fail,
    integer,
    number,
    text,
)
from .config_model import (
    COLUMN_TYPES,
    DEFAULT_NULL_TOKENS,
    SAMPLING_METHODS,
    AssertionSpec,
    BatchSpec,
    ColumnSpec,
    DecisionSpec,
    SamplingSpec,
    SchemaSpec,
)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def mapping(value: Any, location: str, code: str = "GS-CFG-004") -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(code, "期望映射（object）结构", location)
    return value


def parse_column(name: str, raw: Any, location: str) -> ColumnSpec:
    if isinstance(raw, str):
        raw = {"type": raw}
    data = mapping(raw, location)
    unknown = sorted(set(data) - {"type", "required"})
    if unknown:
        fail("GS-CFG-010", f"未知列参数: {', '.join(unknown)}", location)
    col_type = text(data.get("type"), f"{location}.type").lower()
    if col_type not in COLUMN_TYPES:
        fail("GS-CFG-011", f"不支持的列类型: {col_type}", f"{location}.type")
    return ColumnSpec(name, col_type, boolean(data.get("required", False), f"{location}.required"))


def parse_schema(raw: Any) -> SchemaSpec:
    data = mapping(raw, "contract.schema")
    columns_raw = mapping(data.get("columns"), "contract.schema.columns")
    if not columns_raw:
        fail("GS-CFG-010", "columns 不得为空", "contract.schema.columns")
    columns = {
        text(name, "contract.schema.columns.<name>"): parse_column(
            text(name, "contract.schema.columns.<name>"),
            value,
            f"contract.schema.columns.{text(name, 'contract.schema.columns.<name>')}",
        )
        for name, value in columns_raw.items()
    }
    allow_extra = boolean(data.get("allow_extra_columns", False), "contract.schema.allow_extra_columns")
    tokens_raw = data.get("null_tokens", list(DEFAULT_NULL_TOKENS))
    if not isinstance(tokens_raw, list) or any(not isinstance(token, str) for token in tokens_raw):
        fail("GS-CFG-010", "null_tokens 必须是字符串列表", "contract.schema.null_tokens")
    return SchemaSpec(columns, allow_extra, tuple(tokens_raw))


def parse_sampling(raw: Any, schema: SchemaSpec) -> SamplingSpec:
    data = mapping(raw, "contract.sampling")
    method = text(data.get("method"), "contract.sampling.method").lower()
    if method not in SAMPLING_METHODS:
        fail("GS-CFG-020", f"未知抽样方法: {method}", "contract.sampling.method")
    if "seed" not in data:
        fail("GS-CFG-021", "缺少显式随机种子 seed", "contract.sampling.seed")
    seed = integer(data["seed"], "contract.sampling.seed", minimum=0)
    if seed > 2**63 - 1:
        fail("GS-CFG-021", "seed 必须是有符号 64 位整数", "contract.sampling.seed")
    has_size, has_fraction = data.get("size") is not None, data.get("fraction") is not None
    if has_size == has_fraction:
        fail("GS-CFG-020", "size 与 fraction 必须且只能声明一个", "contract.sampling")
    size = integer(data["size"], "contract.sampling.size", minimum=1) if has_size else None
    fraction = (
        number(data["fraction"], "contract.sampling.fraction", low=0.0, high=1.0)
        if has_fraction
        else None
    )
    if fraction == 0:
        fail("GS-CFG-020", "fraction 必须大于 0", "contract.sampling.fraction")
    strata = data.get("strata_field")
    if method == "stratified":
        strata = text(strata, "contract.sampling.strata_field")
        if strata not in schema.columns:
            fail("GS-CFG-020", f"分层字段未在 schema 中声明: {strata}", "contract.sampling.strata_field")
    elif strata is not None:
        fail("GS-CFG-020", "只有 stratified 可声明 strata_field", "contract.sampling.strata_field")
    return SamplingSpec(method, seed, size, fraction, strata)


def validate_assertion_params(
    kind: str, params: dict[str, Any], location: str, schema: SchemaSpec
) -> None:
    """Dispatch to the guard's own declaration; no per-type logic lives here."""
    _validate_assertion_params(kind, params, location, schema)


def parse_assertions(raw: Any, schema: SchemaSpec) -> tuple[AssertionSpec, ...]:
    if not isinstance(raw, list) or not raw:
        fail("GS-CFG-030", "assertions 必须是非空列表", "contract.assertions")
    seen: set[str] = set()
    assertions: list[AssertionSpec] = []
    for index, item in enumerate(raw):
        location = f"contract.assertions[{index}]"
        data = mapping(item, location)
        assertion_id = text(data.get("id"), f"{location}.id")
        if not _ID_RE.match(assertion_id):
            fail("GS-CFG-030", "id 须为小写字母开头的 3-64 位 snake_case", f"{location}.id")
        if assertion_id in seen:
            fail("GS-CFG-032", f"断言 id 重复: {assertion_id}", f"{location}.id")
        seen.add(assertion_id)
        kind = text(data.get("type"), f"{location}.type").lower()
        if kind not in GUARD_TYPES:
            fail("GS-CFG-031", f"未知断言类型: {kind}", f"{location}.type")
        severity = data.get("severity", "fail")
        if severity not in {"fail", "warn"}:
            fail("GS-CFG-030", "severity 只能是 fail 或 warn", f"{location}.severity")
        params = {key: value for key, value in data.items() if key not in {"id", "type", "severity"}}
        validate_assertion_params(kind, params, location, schema)
        assertions.append(AssertionSpec(assertion_id, kind, severity, params))
    return tuple(assertions)


def parse_decision(raw: Any) -> DecisionSpec:
    if raw is None:
        return DecisionSpec()
    data = mapping(raw, "contract.decision")
    unknown = sorted(set(data) - {"block_on_fail", "review_on_warn_count", "review_on_skip_count"})
    if unknown:
        fail("GS-CFG-040", f"未知决策参数: {', '.join(unknown)}", "contract.decision")
    block = boolean(data.get("block_on_fail", True), "contract.decision.block_on_fail")
    warn_count = integer(
        data.get("review_on_warn_count", 1), "contract.decision.review_on_warn_count", minimum=1
    )
    skip_raw = data.get("review_on_skip_count")
    skip_count = (
        None
        if skip_raw is None
        else integer(skip_raw, "contract.decision.review_on_skip_count", minimum=1)
    )
    return DecisionSpec(block, warn_count, skip_count)


def parse_batch(raw: Any, index: int, base_dir: Path) -> BatchSpec:
    location = f"batches[{index}]"
    data = mapping(raw, location)
    batch_id = text(data.get("batch_id"), f"{location}.batch_id")
    if not _ID_RE.match(batch_id):
        fail("GS-CFG-050", "batch_id 须为小写字母开头的 3-64 位 snake_case", f"{location}.batch_id")
    source = text(data.get("source"), f"{location}.source")
    path = (base_dir / source).resolve() if not Path(source).is_absolute() else Path(source).resolve()
    explicit_format = data.get("format")
    if explicit_format is None:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            explicit_format = "csv"
        elif suffix == ".jsonl":
            explicit_format = "jsonl"
        else:
            fail("GS-CFG-050", "无法从扩展名推断格式，请声明 format", f"{location}.format")
    fmt = text(explicit_format, f"{location}.format").lower()
    if fmt not in {"csv", "jsonl"}:
        fail("GS-CFG-050", f"不支持的批次格式: {fmt}", f"{location}.format")
    expected = data.get("expected_rows")
    expected_rows = (
        None if expected is None else integer(expected, f"{location}.expected_rows", minimum=0)
    )
    return BatchSpec(batch_id, source, path, fmt, expected_rows)
