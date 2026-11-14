"""Batch execution pipeline and multi-batch run orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assertions import GuardContext, RowRef, run_guards
from .config import BatchSpec, Contract, RunConfig
from .decision import DecisionResult, decide
from .ledger import init_db, save_judgment
from .loader import BatchData, load_batch
from .report import ReportArtifacts, build_report, write_reports
from .sampler import SampleResult, sample_rows
from .util import canonical_json, make_run_id, sha256_text


@dataclass(frozen=True)
class Judgment:
    run_id: str
    batch_spec: BatchSpec
    batch: BatchData
    sample: SampleResult
    results: tuple[Any, ...]
    decision: DecisionResult
    report: dict[str, Any]
    config_fingerprint: str
    config_snapshot: dict[str, Any]


@dataclass(frozen=True)
class BatchOutcome:
    judgment: Judgment
    artifacts: ReportArtifacts


def contract_fingerprint(contract: Contract, batch_spec: BatchSpec, strict: bool) -> str:
    payload = {
        "config_version": "1.0",
        "strict": strict,
        "contract": contract.to_dict(),
        "batch": batch_spec.to_dict(),
    }
    return sha256_text(canonical_json(payload))


def config_snapshot(contract: Contract, batch_spec: BatchSpec, strict: bool) -> dict[str, Any]:
    return {
        "contract": contract.to_dict(),
        "contract_base_dir": contract.base_dir.as_posix(),
        "batch": batch_spec.to_dict(resolved=True),
        "strict": strict,
    }


def _baseline_paths(contract: Contract) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for assertion in contract.assertions:
        if assertion.type != "drift":
            continue
        reference = assertion.get("baseline")
        if reference in paths:
            continue
        path = Path(reference)
        paths[reference] = path if path.is_absolute() else (contract.base_dir / path).resolve()
    return paths


def _load_baselines(contract: Contract) -> dict[str, list[RowRef]]:
    baselines: dict[str, list[RowRef]] = {}
    for reference, path in _baseline_paths(contract).items():
        fmt = "jsonl" if path.suffix.lower() == ".jsonl" else "csv"
        data = load_batch(
            path,
            batch_id=f"baseline:{reference}",
            schema=contract.schema,
            fmt=fmt,
            strict=False,
            source_label=reference,
        )
        baselines[reference] = [
            RowRef(record_no=number, data=row)
            for number, row in zip(data.record_numbers, data.rows)
        ]
    return baselines


def execute_batch(
    contract: Contract,
    batch_spec: BatchSpec,
    *,
    strict: bool = False,
) -> Judgment:
    """Load, sample, assert, decide, and build the report for one batch."""
    batch = load_batch(
        batch_spec.path,
        batch_id=batch_spec.batch_id,
        schema=contract.schema,
        fmt=batch_spec.format,
        strict=strict,
        source_label=batch_spec.source,
    )
    sample = sample_rows(batch.rows, contract.sampling)
    full_refs = [
        RowRef(record_no=number, data=row)
        for number, row in zip(batch.record_numbers, batch.rows)
    ]
    sample_refs = [full_refs[index] for index in sample.indices]
    context = GuardContext(
        sample=sample_refs,
        full=full_refs,
        baselines=_load_baselines(contract),
        batch={
            "batch_id": batch.batch_id,
            "row_count": batch.row_count,
            "column_count": batch.column_count,
            "expected_rows": batch_spec.expected_rows,
            "source_sha256": batch.source_sha256,
        },
        column_types=contract.schema.column_types,
    )
    results = tuple(run_guards(contract.assertions, context))
    decision = decide(results, contract.decision)
    fingerprint = contract_fingerprint(contract, batch_spec, strict)
    run_id = make_run_id(batch.batch_id, batch.source_sha256, fingerprint)
    report = build_report(
        run_id=run_id,
        contract=contract,
        batch_spec=batch_spec,
        batch=batch,
        sample=sample,
        results=results,
        decision=decision,
        strict=strict,
        config_fingerprint=fingerprint,
    )
    return Judgment(
        run_id=run_id,
        batch_spec=batch_spec,
        batch=batch,
        sample=sample,
        results=results,
        decision=decision,
        report=report,
        config_fingerprint=fingerprint,
        config_snapshot=config_snapshot(contract, batch_spec, strict),
    )


def run_config(
    config: RunConfig,
    *,
    out_dir: str | Path | None = None,
    ledger_path: str | Path | None = None,
    strict: bool | None = None,
) -> list[BatchOutcome]:
    """Execute every configured batch, write reports, and persist audit rows."""
    effective_out = Path(out_dir) if out_dir is not None else config.out_dir
    effective_ledger = Path(ledger_path) if ledger_path is not None else config.ledger_path
    effective_strict = config.strict if strict is None else strict
    connection = init_db(effective_ledger)
    outcomes: list[BatchOutcome] = []
    try:
        for batch_spec in config.batches:
            judgment = execute_batch(config.contract, batch_spec, strict=effective_strict)
            artifacts = write_reports(judgment.report, effective_out)
            save_judgment(
                connection,
                judgment.report,
                config_snapshot=judgment.config_snapshot,
                report_sha256=artifacts.json_sha256,
            )
            outcomes.append(BatchOutcome(judgment, artifacts))
    finally:
        connection.close()
    return outcomes
