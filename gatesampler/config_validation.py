"""Validation helpers for contracts and batch declarations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config_model import (
    COLUMN_TYPES,
    DEFAULT_NULL_TOKENS,
    GUARD_TYPES,
    SAMPLING_METHODS,
    AssertionSpec,
    BatchSpec,
    ColumnSpec,
    DecisionSpec,
    SamplingSpec,
    SchemaSpec,
)
from .errors import GateSamplerError

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def fail(code: str, message: str, location: str) -> None:
    raise GateSamplerError(code, message, location)


def mapping(value: Any, location: str, code: str = "GS-CFG-004") -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(code, "期望映射（object）结构", location)
    return value


def text(value: Any, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        fail("GS-CFG-003", "缺少或非法字符串字段", location)
    return value.strip()


def integer(value: Any, location: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail("GS-CFG-004", "期望整数", location)
    if minimum is not None and value < minimum:
        fail("GS-CFG-004", f"整数不得小于 {minimum}", location)
    return value


def number(
    value: Any, location: str, *, low: float | None = None, high: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail("GS-CFG-004", "期望数值", location)
    result = float(value)
    if low is not None and result < low:
        fail("GS-CFG-004", f"数值不得小于 {low}", location)
    if high is not None and result > high:
        fail("GS-CFG-004", f"数值不得大于 {high}", location)
    return result


def boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        fail("GS-CFG-004", "期望布尔值", location)
    return value


def strings(value: Any, location: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        fail("GS-CFG-004", "期望非空字符串列表" if nonempty else "期望字符串列表", location)
    return [text(item, f"{location}[{index}]") for index, item in enumerate(value)]


def check_keys(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        fail("GS-CFG-030", f"未知断言参数: {', '.join(unknown)}", location)


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
    def column(name: str) -> str:
        value = text(params.get(name), f"{location}.{name}")
        if value not in schema.columns:
            fail("GS-CFG-030", f"字段未在 schema 中声明: {value}", f"{location}.{name}")
        return value

    if kind == "count":
        check_keys(params, {"expected", "tolerance"}, location)
        if "expected" in params:
            integer(params["expected"], f"{location}.expected", minimum=0)
        if "tolerance" in params:
            integer(params["tolerance"], f"{location}.tolerance", minimum=0)
    elif kind == "unique":
        check_keys(params, {"columns", "full_scan"}, location)
        for item in strings(params.get("columns"), f"{location}.columns", nonempty=True):
            if item not in schema.columns:
                fail("GS-CFG-030", f"字段未在 schema 中声明: {item}", f"{location}.columns")
        if "full_scan" in params:
            boolean(params["full_scan"], f"{location}.full_scan")
    elif kind == "domain":
        check_keys(params, {"column", "allowed"}, location)
        column("column")
        allowed = params.get("allowed")
        if not isinstance(allowed, list) or not allowed or any(
            isinstance(value, (dict, list)) for value in allowed
        ):
            fail("GS-CFG-030", "allowed 必须是非空标量列表", f"{location}.allowed")
    elif kind == "range":
        check_keys(params, {"column", "min", "max", "min_length", "max_length"}, location)
        column("column")
        if not any(key in params for key in ("min", "max", "min_length", "max_length")):
            fail("GS-CFG-030", "range 至少需要一个边界", location)
        for key in ("min", "max"):
            if key in params:
                number(params[key], f"{location}.{key}")
        for key in ("min_length", "max_length"):
            if key in params:
                integer(params[key], f"{location}.{key}", minimum=0)
        if "min" in params and "max" in params and params["min"] > params["max"]:
            fail("GS-CFG-030", "min 不得大于 max", location)
        if (
            "min_length" in params
            and "max_length" in params
            and params["min_length"] > params["max_length"]
        ):
            fail("GS-CFG-030", "min_length 不得大于 max_length", location)
    elif kind == "nullrate":
        check_keys(params, {"column", "max_rate"}, location)
        column("column")
        number(params.get("max_rate"), f"{location}.max_rate", low=0.0, high=1.0)
    elif kind == "duplicate":
        check_keys(params, {"columns", "max_rate"}, location)
        for item in strings(params.get("columns"), f"{location}.columns", nonempty=True):
            if item not in schema.columns:
                fail("GS-CFG-030", f"字段未在 schema 中声明: {item}", f"{location}.columns")
        number(params.get("max_rate"), f"{location}.max_rate", low=0.0, high=1.0)
    elif kind == "drift":
        check_keys(params, {"column", "baseline", "threshold", "bins", "metric"}, location)
        column("column")
        text(params.get("baseline"), f"{location}.baseline")
        number(params.get("threshold"), f"{location}.threshold", low=0.0, high=1.0)
        if "bins" in params:
            integer(params["bins"], f"{location}.bins", minimum=2)
        if params.get("metric", "max_share_delta") != "max_share_delta":
            fail("GS-CFG-030", "目前只支持 max_share_delta", f"{location}.metric")


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
