"""Local CSV/JSONL loading, schema-driven coercion, and structural warnings."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .config_model import ColumnSpec, SchemaSpec
from .errors import GateSamplerError
from .util import sha256_bytes


@dataclass(frozen=True)
class LoadWarning:
    code: str
    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "location": self.location}


@dataclass
class BatchData:
    batch_id: str
    source: str
    format: str
    encoding: str
    columns: list[str]
    rows: list[dict[str, Any]]
    record_numbers: list[int]
    source_sha256: str
    warnings: list[LoadWarning]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)


def _warn(warnings: list[LoadWarning], code: str, message: str, location: str | None = None) -> None:
    warnings.append(LoadWarning(code, message, location))


def _convert(
    value: Any,
    spec: ColumnSpec,
    row_no: int,
    null_tokens: set[str],
    warnings: list[LoadWarning],
) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in null_tokens:
        return None
    kind = spec.type
    location = f"record={row_no},column={spec.name}"
    try:
        if kind == "str":
            return value if isinstance(value, str) else str(value)
        if kind == "int":
            if isinstance(value, bool):
                raise ValueError("bool 不是 int")
            return value if isinstance(value, int) else int(str(value).strip())
        if kind == "float":
            if isinstance(value, bool):
                raise ValueError("bool 不是 float")
            return float(value)
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            text = str(value).strip().lower()
            if text in {"true", "1", "yes", "y"}:
                return True
            if text in {"false", "0", "no", "n"}:
                return False
            raise ValueError("无法识别布尔值")
        if kind == "date":
            date.fromisoformat(str(value).strip())
            return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError) as exc:
        _warn(warnings, "GS-LOAD-103", f"类型转换失败（期望 {kind}）: {exc}", location)
        return value
    return value


class _LineNumberingIterator:
    """Yield source lines while tracking the 1-based physical line of the next line.

    ``csv.reader`` consumes one or more source lines per record (a quoted field may
    span several lines); exposing the line number before each fetch lets the caller
    record the physical line at which a record starts.
    """

    def __init__(self, text: str):
        self._lines = iter(io.StringIO(text, newline=""))
        self.line_no = 0

    def __iter__(self) -> _LineNumberingIterator:
        return self

    def __next__(self) -> str:
        line = next(self._lines)
        self.line_no += 1
        return line


def _load_csv(
    text: str, schema: SchemaSpec, warnings: list[LoadWarning]
) -> tuple[list[str], list[dict[str, Any]], list[int]]:
    if not text.strip():
        _warn(warnings, "GS-LOAD-104", "CSV 批次为空文件")
        return [], [], []
    try:
        line_iter = _LineNumberingIterator(text)
        reader = csv.reader(line_iter)
        try:
            header = next(reader)
        except StopIteration:
            _warn(warnings, "GS-LOAD-104", "CSV 批次为空文件")
            return [], [], []
        columns = list(header)
        if len(columns) != len(set(columns)):
            _warn(warnings, "GS-LOAD-105", "CSV 表头包含重复列名", "header")
        rows: list[dict[str, Any]] = []
        record_numbers: list[int] = []
        null_tokens = set(schema.null_tokens)
        while True:
            start_line = line_iter.line_no + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            # csv.reader 对完全空白行返回空列表；DictReader 会直接跳过这类行，
            # 因此记录号保持为数据行在源文件中的真实物理行号（第 1 行为表头）。
            if not values:
                continue
            raw = {
                name: (values[index] if index < len(values) else None)
                for index, name in enumerate(columns)
            }
            row: dict[str, Any] = {}
            for name, spec in schema.columns.items():
                row[name] = _convert(raw.get(name), spec, start_line, null_tokens, warnings)
            for name in columns:
                if name not in row:
                    row[name] = raw.get(name)
            rows.append(row)
            record_numbers.append(start_line)
    except csv.Error as exc:
        raise GateSamplerError("GS-LOAD-003", f"CSV 解析失败: {exc}") from exc
    if not rows:
        _warn(warnings, "GS-LOAD-104", "CSV 只有表头，没有数据行")
    return columns, rows, record_numbers


def _load_jsonl(
    text: str, schema: SchemaSpec, warnings: list[LoadWarning]
) -> tuple[list[str], list[dict[str, Any]], list[int]]:
    rows: list[dict[str, Any]] = []
    record_numbers: list[int] = []
    columns: list[str] = []
    null_tokens = set(schema.null_tokens)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            _warn(warnings, "GS-LOAD-004", f"JSONL 行解析失败: {exc.msg}", f"line={line_no}")
            continue
        if not isinstance(raw, dict):
            _warn(warnings, "GS-LOAD-005", "JSONL 行必须是对象", f"line={line_no}")
            continue
        for key in raw:
            if key not in columns:
                columns.append(key)
        row: dict[str, Any] = {}
        for name, spec in schema.columns.items():
            row[name] = _convert(raw.get(name), spec, line_no, null_tokens, warnings)
        for name, value in raw.items():
            if name not in row:
                row[name] = value
        rows.append(row)
        record_numbers.append(line_no)
    if not rows:
        _warn(warnings, "GS-LOAD-104", "JSONL 批次为空或没有有效对象行")
    return columns, rows, record_numbers


def _check_columns(
    columns: list[str], schema: SchemaSpec, warnings: list[LoadWarning]
) -> None:
    missing = [name for name in schema.columns if name not in columns]
    if missing:
        required = [name for name in missing if schema.columns[name].required]
        label = "必填" if required else ""
        _warn(
            warnings,
            "GS-LOAD-101",
            f"缺少{label}列: {', '.join(missing)}",
            "header",
        )
    if not schema.allow_extra_columns:
        extra = [name for name in columns if name not in schema.columns]
        if extra:
            _warn(warnings, "GS-LOAD-102", f"存在未声明的多余列: {', '.join(extra)}", "header")


def load_batch(
    path: str | Path,
    *,
    batch_id: str,
    schema: SchemaSpec,
    fmt: str | None = None,
    strict: bool = False,
    source_label: str | None = None,
) -> BatchData:
    """Load one local batch; never touches the network."""
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise GateSamplerError("GS-LOAD-001", f"无法读取批次文件: {exc}", file_path.as_posix()) from exc
    source_sha256 = sha256_bytes(raw)
    actual_format = (fmt or file_path.suffix.lstrip(".")).lower()
    if actual_format not in {"csv", "jsonl"}:
        raise GateSamplerError(
            "GS-LOAD-002", f"不支持的批次格式: {actual_format}", file_path.as_posix()
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GateSamplerError("GS-LOAD-002", f"批次不是有效 UTF-8: {exc}", file_path.as_posix()) from exc
    warnings: list[LoadWarning] = []
    if actual_format == "csv":
        columns, rows, record_numbers = _load_csv(text, schema, warnings)
    else:
        columns, rows, record_numbers = _load_jsonl(text, schema, warnings)
    _check_columns(columns, schema, warnings)
    if strict and warnings:
        first = warnings[0]
        raise GateSamplerError(
            first.code,
            f"strict 模式将加载警告升级为错误: {first.message}",
            first.location,
        )
    return BatchData(
        batch_id=batch_id,
        source=source_label or file_path.as_posix(),
        format=actual_format,
        encoding="utf-8-sig",
        columns=columns,
        rows=rows,
        record_numbers=record_numbers,
        source_sha256=source_sha256,
        warnings=warnings,
    )
