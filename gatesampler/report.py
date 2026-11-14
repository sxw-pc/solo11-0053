"""Stable JSON and Markdown judgment reports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ReportArtifacts:
    json_path: Path
    markdown_path: Path
    json_sha256: str
    markdown_sha256: str


def build_report(
    *,
    run_id: str,
    contract: Any,
    batch_spec: Any,
    batch: Any,
    sample: Any,
    results: Sequence[Any],
    decision: Any,
    strict: bool,
    config_fingerprint: str,
) -> dict[str, Any]:
    assertion_dicts = [result.to_dict() for result in results]
    non_pass = Counter(
        result.code for result in results if result.status in {"fail", "warn", "skip"}
    )
    load_codes = Counter(warning.code for warning in batch.warnings)
    decision_codes = Counter(reason["rule_code"] for reason in decision.reasons)
    sampling = sample.to_dict()
    sampling["sample_record_numbers"] = [batch.record_numbers[i] for i in sample.indices]
    return {
        "report_version": "1.0",
        "judgment_id": run_id,
        "config_fingerprint": config_fingerprint,
        "gate": {
            "gate_id": contract.gate_id,
            "contract_version": contract.contract_version,
            "description": contract.description,
        },
        "batch": {
            "batch_id": batch.batch_id,
            "source": batch_spec.source,
            "format": batch.format,
            "encoding": batch.encoding,
            "row_count": batch.row_count,
            "column_count": batch.column_count,
            "columns": list(batch.columns),
            "expected_rows": batch_spec.expected_rows,
            "source_sha256": batch.source_sha256,
            "load_warning_count": len(batch.warnings),
            "load_warnings": [warning.to_dict() for warning in batch.warnings],
            "strict": strict,
        },
        "sampling": sampling,
        "assertions": assertion_dicts,
        "decision": decision.to_dict(),
        "error_codes": {
            "load": dict(sorted(load_codes.items())),
            "assertions": dict(sorted(non_pass.items())),
            "decision": dict(sorted(decision_codes.items())),
        },
    }


def render_json(report: dict[str, Any]) -> bytes:
    return canonical_json(report, pretty=True).encode("utf-8")


def _text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value.replace("|", "\\|").replace("\n", " ")
    if isinstance(value, (dict, list)):
        return canonical_json(value).replace("|", "\\|").replace("\n", " ")
    return str(value)


def _code_table(codes: dict[str, int]) -> list[str]:
    if not codes:
        return ["_无_", ""]
    lines = ["| 错误码 | 次数 |", "| --- | ---: |"]
    lines.extend(f"| `{code}` | {count} |" for code, count in sorted(codes.items()))
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    batch = report["batch"]
    sampling = report["sampling"]
    decision = report["decision"]
    gate = report["gate"]
    lines = [
        f"# 闸门判定摘要 — {batch['batch_id']}",
        "",
        f"> 决策：**{decision['outcome']}**  ",
        f"> 闸门：`{gate['gate_id']}` / 契约 `{gate['contract_version']}`  ",
        f"> 判定 ID：`{report['judgment_id']}`",
        "",
        "## 批次与来源",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
        f"| 批次 | `{batch['batch_id']}` |",
        f"| 来源 | `{_text(batch['source'])}` |",
        f"| 格式 | `{batch['format']}` |",
        f"| 行数 / 列数 | {batch['row_count']} / {batch['column_count']} |",
        f"| 声明行数 | {_text(batch['expected_rows'])} |",
        f"| 文件 SHA-256 | `{batch['source_sha256']}` |",
        f"| 严格模式 | {'是' if batch['strict'] else '否'} |",
        f"| 加载警告 | {batch['load_warning_count']} |",
        "",
        "## 抽样参数",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
        f"| 方法 | `{sampling['method']}` |",
        f"| 种子 | `{sampling['seed']}` |",
        f"| 请求样本量 / 实际 | {sampling['size_requested']} / {sampling['size_actual']} |",
        f"| 样本 SHA-256 | `{sampling['sample_fingerprint']}` |",
        f"| 样本记录号 | {_text(sampling['sample_record_numbers'])} |",
        "",
        "## 断言结果",
        "",
        "| 断言 | 类型 | 状态 | 指标 | 阈值 | 错误码 | 说明 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for assertion in report["assertions"]:
        lines.append(
            "| {id} | {type} | **{status}** | {metric} | {threshold} | `{code}` | {message} |".format(
                id=_text(assertion["assertion_id"]),
                type=_text(assertion["type"]),
                status=_text(assertion["status"]),
                metric=_text(assertion["metric"]),
                threshold=_text(assertion["threshold"]),
                code=_text(assertion["code"]),
                message=_text(assertion["message"]),
            )
        )
    counts = decision["counts"]
    lines.extend(
        [
            "",
            f"统计：pass={counts['pass']} fail={counts['fail']} warn={counts['warn']} skip={counts['skip']}",
            "",
            "## 决策理由链",
            "",
            "| 规则码 | 规则 | 贡献态 | 断言 | 说明 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for reason in decision["reasons"]:
        lines.append(
            "| `{rule_code}` | {rule} | {outcome} | {assertion} | {message} |".format(
                rule_code=_text(reason["rule_code"]),
                rule=_text(reason["rule"]),
                outcome=_text(reason["outcome"]),
                assertion=_text(reason.get("assertion_id")),
                message=_text(reason["message"]),
            )
        )
    failed = [item for item in report["assertions"] if item["status"] != "pass"]
    if failed:
        lines.extend(["", "## 非通过断言证据", ""])
        for assertion in failed:
            evidence = canonical_json(assertion["evidence"], pretty=True).rstrip()
            lines.extend(
                [
                    f"### `{assertion['assertion_id']}` ({assertion['status']})",
                    "",
                    "```json",
                    evidence,
                    "```",
                    "",
                ]
            )
    if batch["load_warnings"]:
        lines.extend(["## 加载警告", "", "| 错误码 | 位置 | 说明 |", "| --- | --- | --- |"])
        for warning in batch["load_warnings"]:
            lines.append(
                f"| `{warning['code']}` | {_text(warning['location'])} | {_text(warning['message'])} |"
            )
        lines.append("")
    lines.extend(["## 错误码统计", "", "### 加载", ""])
    lines.extend(_code_table(report["error_codes"]["load"]))
    lines.extend(["### 断言", ""])
    lines.extend(_code_table(report["error_codes"]["assertions"]))
    lines.extend(["### 决策", ""])
    lines.extend(_code_table(report["error_codes"]["decision"]))
    lines.extend(
        [
            "## 复现",
            "",
            "```bash",
            f"python -m gatesampler replay --ledger out/gate_sampler.sqlite3 --run-id {report['judgment_id']}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(report: dict[str, Any], out_dir: str | Path) -> ReportArtifacts:
    report_dir = Path(out_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    batch_id = report["batch"]["batch_id"]
    json_bytes = render_json(report)
    markdown_bytes = render_markdown(report).encode("utf-8")
    json_path = report_dir / f"{batch_id}.json"
    markdown_path = report_dir / f"{batch_id}.md"
    json_path.write_bytes(json_bytes)
    markdown_path.write_bytes(markdown_bytes)
    return ReportArtifacts(
        json_path=json_path,
        markdown_path=markdown_path,
        json_sha256=sha256_bytes(json_bytes),
        markdown_sha256=sha256_bytes(markdown_bytes),
    )
