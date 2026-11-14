"""Immutable data models for run configurations and gate contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_VERSION = "1.0"
CONTRACT_VERSIONS = frozenset({"1.0"})
COLUMN_TYPES = frozenset({"int", "float", "bool", "str", "date"})
SAMPLING_METHODS = frozenset({"systematic", "stratified", "reservoir"})
GUARD_TYPES = frozenset({"count", "unique", "domain", "range", "nullrate", "duplicate", "drift"})
DEFAULT_NULL_TOKENS = ("", "NULL", "null", "NA", "N/A", "None")


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "required": self.required}


@dataclass(frozen=True)
class SchemaSpec:
    columns: dict[str, ColumnSpec]
    allow_extra_columns: bool = False
    null_tokens: tuple[str, ...] = DEFAULT_NULL_TOKENS

    @property
    def column_types(self) -> dict[str, str]:
        return {name: spec.type for name, spec in self.columns.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_extra_columns": self.allow_extra_columns,
            "columns": {name: spec.to_dict() for name, spec in self.columns.items()},
            "null_tokens": list(self.null_tokens),
        }


@dataclass(frozen=True)
class SamplingSpec:
    method: str
    seed: int
    size: int | None = None
    fraction: float | None = None
    strata_field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "seed": self.seed,
            "size": self.size,
            "fraction": self.fraction,
            "strata_field": self.strata_field,
        }


@dataclass(frozen=True)
class AssertionSpec:
    assertion_id: str
    type: str
    severity: str
    params: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.params[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.assertion_id, "type": self.type, "severity": self.severity, **self.params}


@dataclass(frozen=True)
class DecisionSpec:
    block_on_fail: bool = True
    review_on_warn_count: int = 1
    review_on_skip_count: int | None = None
    block_on_fail_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        # 未声明的可选字段不进入字典，保证未使用新规则时配置快照逐字节稳定。
        data = {
            "block_on_fail": self.block_on_fail,
            "review_on_warn_count": self.review_on_warn_count,
            "review_on_skip_count": self.review_on_skip_count,
        }
        if self.block_on_fail_count is not None:
            data["block_on_fail_count"] = self.block_on_fail_count
        return data


@dataclass(frozen=True)
class Contract:
    contract_version: str
    gate_id: str
    schema: SchemaSpec
    sampling: SamplingSpec
    assertions: tuple[AssertionSpec, ...]
    decision: DecisionSpec
    base_dir: Path
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "gate_id": self.gate_id,
            "description": self.description,
            "schema": self.schema.to_dict(),
            "sampling": self.sampling.to_dict(),
            "assertions": [item.to_dict() for item in self.assertions],
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True)
class BatchSpec:
    batch_id: str
    source: str
    path: Path
    format: str
    expected_rows: int | None = None

    def to_dict(self, *, resolved: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "batch_id": self.batch_id,
            "source": self.source,
            "format": self.format,
            "expected_rows": self.expected_rows,
        }
        if resolved:
            data["resolved_source"] = self.path.as_posix()
        return data


@dataclass(frozen=True)
class RunConfig:
    version: str
    contract: Contract
    batches: tuple[BatchSpec, ...]
    out_dir: Path
    ledger_path: Path
    strict: bool
    source_path: Path
