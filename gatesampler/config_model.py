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
    # A single field name or an ordered tuple of field names for cross-stratification.
    strata_field: str | tuple[str, ...] | None = None
    per_stratum_min: int | None = None

    @property
    def strata_fields(self) -> tuple[str, ...]:
        """Normalize ``strata_field`` into an ordered tuple of field names."""
        if self.strata_field is None:
            return ()
        if isinstance(self.strata_field, str):
            return (self.strata_field,)
        return tuple(self.strata_field)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "method": self.method,
            "seed": self.seed,
            "size": self.size,
            "fraction": self.fraction,
            "strata_field": (
                list(self.strata_field) if isinstance(self.strata_field, tuple) else self.strata_field
            ),
        }
        # Keep historical snapshots/reports byte-identical when the feature is unused.
        if self.per_stratum_min is not None:
            data["per_stratum_min"] = self.per_stratum_min
        return data


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_on_fail": self.block_on_fail,
            "review_on_warn_count": self.review_on_warn_count,
            "review_on_skip_count": self.review_on_skip_count,
        }


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
