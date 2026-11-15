"""Replay a persisted judgment from its contract snapshot and seed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import batch_from_dict, contract_from_dict
from .errors import GateSamplerError
from .ledger import get_judgment, init_db, latest_judgment
from .report import render_json
from .runner import Judgment, execute_batch
from .util import canonical_json, sha256_bytes

_ASSERTION_FIELDS = (
    "assertion_id",
    "type",
    "status",
    "code",
    "metric",
    "threshold",
    "evidence",
    "message",
)

# 断言证据中承载“记录号”语义的键（其值为源文件物理行号）。
# 记录号语义升级（CSV 由数据行序号改为物理行号）只会改变这些位置。
_RECORD_NUMBER_EVIDENCE_KEYS = frozenset(
    {"record_no", "rows", "null_rows"}
)
# 抽样报告中承载记录号语义的键；indices 与 sample_fingerprint 不在其中，保持原样比较。
_RECORD_NUMBER_SAMPLING_KEYS = frozenset({"sample_record_numbers"})


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    consistent: bool
    mismatches: tuple[str, ...]
    judgment: Judgment
    stored_report_sha256: str
    recomputed_report_sha256: str
    record_number_upgrade: bool = False
    upgrade_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "consistent": self.consistent,
            "mismatches": list(self.mismatches),
            "stored_report_sha256": self.stored_report_sha256,
            "recomputed_report_sha256": self.recomputed_report_sha256,
            "record_number_upgrade": self.record_number_upgrade,
            "upgrade_notes": list(self.upgrade_notes),
            "decision": self.judgment.decision.outcome,
            "batch_id": self.judgment.batch_spec.batch_id,
            "sample_fingerprint": self.judgment.sample.sample_fingerprint,
        }


def _signature(assertion: dict[str, Any]) -> str:
    return canonical_json({field: assertion.get(field) for field in _ASSERTION_FIELDS})


def _strip_record_numbers(value: Any) -> Any:
    """Return a copy of an evidence/sampling structure with record-number leaves removed."""
    if isinstance(value, dict):
        return {
            key: _strip_record_numbers(item)
            for key, item in value.items()
            if key not in _RECORD_NUMBER_EVIDENCE_KEYS
        }
    if isinstance(value, list):
        return [_strip_record_numbers(item) for item in value]
    return value


def _normalized_signature(assertion: dict[str, Any]) -> str:
    """Signature ignoring only the record-number fields of the evidence."""
    payload = {field: assertion.get(field) for field in _ASSERTION_FIELDS}
    payload["evidence"] = _strip_record_numbers(payload.get("evidence"))
    return canonical_json(payload)


def _sampling_without_record_numbers(sampling: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in sampling.items()
        if key not in _RECORD_NUMBER_SAMPLING_KEYS
    }


def _resolve_record(ledger_path: str | Path, run_id: str | None, batch_id: str | None) -> dict[str, Any]:
    connection = init_db(ledger_path)
    try:
        if run_id:
            record = get_judgment(connection, run_id)
        elif batch_id:
            record = latest_judgment(connection, batch_id)
        else:
            raise GateSamplerError(
                "GS-LGR-001", "重放需要 --run-id 或 --batch-id", "replay"
            )
        if record is None:
            raise GateSamplerError("GS-LGR-002", "台账中未找到匹配的判定", "replay")
        return record
    finally:
        connection.close()


def replay_judgment(
    ledger_path: str | Path,
    *,
    run_id: str | None = None,
    batch_id: str | None = None,
) -> ReplayResult:
    record = _resolve_record(ledger_path, run_id, batch_id)
    snapshot = record.get("config_snapshot")
    if not snapshot:
        raise GateSamplerError("GS-LGR-003", "台账记录缺少配置快照", record["run_id"])
    contract = contract_from_dict(snapshot["contract"], snapshot["contract_base_dir"])
    batch_spec = batch_from_dict(snapshot["batch"], snapshot["contract_base_dir"])
    judgment = execute_batch(contract, batch_spec, strict=bool(snapshot.get("strict", False)))
    mismatches: list[str] = []

    def compare(label: str, stored: Any, current: Any) -> None:
        if stored != current:
            mismatches.append(f"{label}: 台账={stored!r} 重放={current!r}")

    compare("run_id", record["run_id"], judgment.run_id)
    compare("source_sha256", record["source_sha256"], judgment.batch.source_sha256)
    compare("config_fingerprint", record["config_fingerprint"], judgment.config_fingerprint)
    compare("sampling.seed", record["seed"], judgment.sample.seed)
    compare(
        "sampling.sample_fingerprint",
        record["sampling"].get("sample_fingerprint"),
        judgment.sample.sample_fingerprint,
    )
    # sample.indices 与 sample_fingerprint 必须原样一致；仅 sample_record_numbers
    # 属于记录号语义，剥离后再比较一次，用于识别记录号语义升级。
    sampling_record_number_only = (
        record["sampling"] != judgment.report["sampling"]
        and _sampling_without_record_numbers(record["sampling"])
        == _sampling_without_record_numbers(judgment.report["sampling"])
    )
    compare("decision", record["decision"], judgment.decision.outcome)
    stored_assertions = record.get("assertions", [])
    current_assertions = [result.to_dict() for result in judgment.results]
    stored_signatures = [_signature(item) for item in stored_assertions]
    current_signatures = [_signature(item) for item in current_assertions]
    compare("assertions", stored_signatures, current_signatures)
    assertion_structure_match = (
        [_normalized_signature(item) for item in stored_assertions]
        == [_normalized_signature(item) for item in current_assertions]
    )
    current_json = render_json(judgment.report)
    current_report_sha = sha256_bytes(current_json)
    compare("report_sha256", record["report_sha256"], current_report_sha)

    # 兼容策略：修复前落账的旧 CSV 台账使用“数据行序号”作为记录号，修复后重放
    # 仅会在断言证据记录号与报告哈希上产生差异（sample_record_numbers 不参与逐
    # 字段 compare，其差异已包含在报告哈希中）。若剥离所有记录号字段后断言结构
    # 完全一致，且源文件/配置/抽样（含 indices 与 fingerprint）/决策均未变化，
    # 则该差异属于“记录号语义升级”的预期签名变化，而非真实数据漂移。
    mismatch_labels = {item.split(":", 1)[0] for item in mismatches}
    record_number_upgrade = bool(mismatches) and mismatch_labels <= {"assertions", "report_sha256"}
    if "assertions" in mismatch_labels:
        record_number_upgrade = record_number_upgrade and assertion_structure_match
    else:
        record_number_upgrade = record_number_upgrade and sampling_record_number_only
    record_number_upgrade = (
        record_number_upgrade
        and judgment.batch.format == "csv"
        and record["source_sha256"] == judgment.batch.source_sha256
        and record["sampling"].get("sample_fingerprint")
        == judgment.sample.sample_fingerprint
        and record["decision"] == judgment.decision.outcome
    )
    upgrade_notes: tuple[str, ...] = ()
    if record_number_upgrade:
        notes = [
            "记录号语义已升级：CSV 记录号由数据行序号改为源文件物理行号（第 1 行为表头）",
            "剥离记录号字段后断言证据与抽样结构完全一致，源文件未变化，属于预期签名变化",
        ]
        if "report_sha256" in mismatch_labels:
            notes.append(
                f"报告哈希差异为记录号升级的连带结果: 台账={record['report_sha256']} "
                f"重放={current_report_sha}"
            )
        upgrade_notes = tuple(notes)

    consistent = not mismatches
    return ReplayResult(
        run_id=record["run_id"],
        consistent=consistent,
        mismatches=tuple(mismatches),
        judgment=judgment,
        stored_report_sha256=record["report_sha256"],
        recomputed_report_sha256=current_report_sha,
        record_number_upgrade=record_number_upgrade,
        upgrade_notes=upgrade_notes,
    )
