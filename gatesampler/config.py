"""Public YAML configuration loader for GateSampler contracts and batches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config_model import (  # noqa: F401  (re-exported public model API)
    COLUMN_TYPES,
    CONFIG_VERSION,
    CONTRACT_VERSIONS,
    DEFAULT_DRIFT_METRIC,
    DEFAULT_NULL_TOKENS,
    DRIFT_METRICS,
    GUARD_TYPES,
    SAMPLING_METHODS,
    AssertionSpec,
    BatchSpec,
    ColumnSpec,
    Contract,
    DecisionSpec,
    RunConfig,
    SamplingSpec,
    SchemaSpec,
)
from .config_validation import (
    boolean,
    fail,
    integer,
    mapping,
    parse_assertions,
    parse_batch,
    parse_decision,
    parse_sampling,
    parse_schema,
    text,
)
from .errors import (
    GateSamplerError,  # noqa: F401  (part of this module's public surface)
)


def _read_yaml(path: Path, location: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:
        fail("GS-CFG-001", f"无法读取 YAML: {exc}", location)
    except yaml.YAMLError as exc:
        fail("GS-CFG-001", f"YAML 解析失败: {exc}", location)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_contract(raw: Any, base_dir: Path) -> Contract:
    data = mapping(raw, "contract")
    version = text(data.get("contract_version"), "contract.contract_version")
    if version not in CONTRACT_VERSIONS:
        fail("GS-CFG-002", f"不支持的契约版本: {version}", "contract.contract_version")
    gate_id = text(data.get("gate_id"), "contract.gate_id")
    schema = parse_schema(data.get("schema"))
    sampling = parse_sampling(data.get("sampling"), schema)
    assertions = parse_assertions(data.get("assertions"), schema)
    decision = parse_decision(data.get("decision"))
    description = data.get("description", "")
    if not isinstance(description, str):
        fail("GS-CFG-003", "description 必须是字符串", "contract.description")
    return Contract(version, gate_id, schema, sampling, assertions, decision, base_dir, description)


def contract_from_dict(data: dict[str, Any], base_dir: str | Path) -> Contract:
    """Rebuild and revalidate a contract from a ledger snapshot."""
    return _parse_contract(data, Path(base_dir))


def batch_from_dict(data: dict[str, Any], base_dir: str | Path | None = None) -> BatchSpec:
    """Rebuild a batch spec from a ledger snapshot, honoring resolved_source."""
    resolved = data.get("resolved_source")
    if resolved:
        path = Path(str(resolved)).resolve()
    elif base_dir is not None:
        path = (Path(base_dir) / text(data.get("source"), "batch.source")).resolve()
    else:
        fail("GS-CFG-050", "快照缺少 resolved_source", "batch")
    source = text(data.get("source"), "batch.source")
    fmt = text(data.get("format"), "batch.format").lower()
    if fmt not in {"csv", "jsonl"}:
        fail("GS-CFG-050", f"不支持的批次格式: {fmt}", "batch.format")
    expected = data.get("expected_rows")
    expected_rows = (
        None if expected is None else integer(expected, "batch.expected_rows", minimum=0)
    )
    return BatchSpec(
        text(data.get("batch_id"), "batch.batch_id"), source, path, fmt, expected_rows
    )


def load_run_config(path: str | Path, strict: bool | None = None) -> RunConfig:
    config_path = Path(path).resolve()
    raw = _read_yaml(config_path, str(config_path))
    data = mapping(raw, "config", "GS-CFG-001")
    version = text(data.get("version"), "config.version")
    if version != CONFIG_VERSION:
        fail("GS-CFG-002", f"不支持的配置版本: {version}", "config.version")
    contract_ref = text(data.get("contract"), "config.contract")
    contract_path = (config_path.parent / contract_ref).resolve()
    contract_raw = _read_yaml(contract_path, "contract")
    overrides = data.get("contract_overrides", {})
    if not isinstance(overrides, dict):
        fail("GS-CFG-060", "contract_overrides 必须是映射", "config.contract_overrides")
    contract_data = _deep_merge(mapping(contract_raw, "contract"), overrides)
    contract = _parse_contract(contract_data, contract_path.parent)
    batches_raw = data.get("batches")
    if not isinstance(batches_raw, list) or not batches_raw:
        fail("GS-CFG-050", "batches 必须是非空列表", "config.batches")
    batches = tuple(
        parse_batch(item, index, config_path.parent) for index, item in enumerate(batches_raw)
    )
    ids = [batch.batch_id for batch in batches]
    if len(ids) != len(set(ids)):
        fail("GS-CFG-051", "batch_id 重复", "config.batches")
    output = mapping(data.get("output", {}), "config.output")
    out_dir = Path(text(output.get("dir", "out"), "config.output.dir"))
    ledger_raw = output.get("ledger")
    if ledger_raw is not None and not isinstance(ledger_raw, str):
        fail("GS-CFG-004", "output.ledger 必须是路径字符串", "config.output.ledger")
    ledger_path = Path(ledger_raw) if ledger_raw is not None else out_dir / "gate_sampler.sqlite3"
    configured_strict = boolean(data.get("strict", False), "config.strict")
    resolved_strict = configured_strict if strict is None else strict
    return RunConfig(version, contract, batches, out_dir, ledger_path, resolved_strict, config_path)
