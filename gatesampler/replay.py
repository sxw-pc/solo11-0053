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


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    consistent: bool
    mismatches: tuple[str, ...]
    judgment: Judgment
    stored_report_sha256: str
    recomputed_report_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "consistent": self.consistent,
            "mismatches": list(self.mismatches),
            "stored_report_sha256": self.stored_report_sha256,
            "recomputed_report_sha256": self.recomputed_report_sha256,
            "decision": self.judgment.decision.outcome,
            "batch_id": self.judgment.batch_spec.batch_id,
            "sample_fingerprint": self.judgment.sample.sample_fingerprint,
        }


def _signature(assertion: dict[str, Any]) -> str:
    return canonical_json({field: assertion.get(field) for field in _ASSERTION_FIELDS})


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
    compare("decision", record["decision"], judgment.decision.outcome)
    stored_assertions = record.get("assertions", [])
    current_assertions = [result.to_dict() for result in judgment.results]
    compare(
        "assertions",
        [_signature(item) for item in stored_assertions],
        [_signature(item) for item in current_assertions],
    )
    current_json = render_json(judgment.report)
    current_report_sha = sha256_bytes(current_json)
    compare("report_sha256", record["report_sha256"], current_report_sha)
    consistent = not mismatches
    return ReplayResult(
        run_id=record["run_id"],
        consistent=consistent,
        mismatches=tuple(mismatches),
        judgment=judgment,
        stored_report_sha256=record["report_sha256"],
        recomputed_report_sha256=current_report_sha,
    )
